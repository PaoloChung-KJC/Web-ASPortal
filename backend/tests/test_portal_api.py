"""고객 API 검증: 메모리 SQLite + 임시 사진 폴더, 운영 DB 접근 없음.

crm-rma test_as_portal_api.py 를 옮긴 것. 원본은 직원 API(/api/as/...)를 함께 띄워 상태를 바꿨지만
여기에는 직원 API 가 없으므로 같은 상태를 DB 에 직접 만든다(staff_* 도우미).
"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    AsCase, AsCaseLog, AsCasePhoto, AsPortalAccount, CommonCode, CommonCodeGroup, Company, RecordRevision,
)
from app.routers import portal as portal_mod
from app.routers.portal import router
from app.services import photo as photo_mod
from app.services.case_rules import today_kst
from app.services.portal_auth import (
    LOGIN_WINDOW_MAX, check_login_rate, create_portal_token, hash_password, reset_login_rate,
)

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PW = "password1"


class PortalApiTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add_all([Company(id=1, name="가고객"), Company(id=2, name="나고객")])
        for gc, codes in {"AS_PRODUCT_GROUP": [("T10", "T10형"), ("K13", "K13형")],
                          "AS_KIND": [("CS", "CS (현장)"), ("AS", "AS (입고 수리)")], "AS_FAULT": [("MAT", "자재")]}.items():
            grp = CommonCodeGroup(group_code=gc, group_name=gc)
            self.db.add(grp)
            self.db.flush()
            for n, (c, d) in enumerate(codes):
                self.db.add(CommonCode(group_id=grp.id, code=c, display_name=d, sort_order=n, use_yn="Y"))
        self.accA = self.account(1, "usera", must_change="N")
        self.accB = self.account(2, "userb", must_change="N")
        self.db.commit()
        reset_login_rate()  # 로그인 횟수 제한은 프로세스 전체 — 테스트끼리 섞이지 않게
        self.addCleanup(reset_login_rate)
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(photo_mod, "AS_UPLOAD_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.app = app
        self.client = TestClient(app)
        self.tokA = create_portal_token(self.accA)
        self.tokB = create_portal_token(self.accB)

    # ---- 직원(CRM)이 하는 일을 DB 에 직접 만든다 ----
    def staff_save(self, cid, **fields):
        """직원 저장: 칸을 고치고, 품번이 채워지면 접수 확인(open), 수정 번호 +1."""
        row = self.db.get(AsCase, cid)
        for k, v in fields.items():
            setattr(row, k, v)
        if row.status == "received" and (row.part_no or "").strip():
            row.status = "open"
            row.confirmed_at = datetime.now(timezone.utc)
        rev = self.db.get(RecordRevision, ("as.case", str(cid)))
        if rev is None:
            rev = RecordRevision(scope="as.case", ref_key=str(cid), rev=0)
            self.db.add(rev)
        rev.rev = (rev.rev or 0) + 1
        rev.updated_by_name = "직원1"
        self.db.commit()

    def staff_status(self, cid, action):
        row = self.db.get(AsCase, cid)
        if action == "close":
            row.status, row.closed_at = "closed", datetime.now(timezone.utc)
        elif action == "done":
            row.status, row.closed_at = "done", datetime.now(timezone.utc)
        elif action == "reopen":   # 직원이 되돌리면 고객 취소 표시도 비운다
            row.status, row.closed_at, row.canceled_at = "open", None, None
        else:
            raise ValueError(action)
        self.db.commit()

    def staff_photo(self, cid):
        p = AsCasePhoto(case_id=cid, stored_name="staffphoto.jpg", orig_name="s.jpg", content_type="image/jpeg",
                        size=len(JPEG), uploaded_by="staff", uploaded_by_name="직원1")
        self.db.add(p)
        self.db.commit()
        (self.tmp / str(cid)).mkdir(parents=True, exist_ok=True)
        (self.tmp / str(cid) / p.stored_name).write_bytes(JPEG)
        return p.id

    def staff_log(self, cid, content):
        self.db.add(AsCaseLog(case_id=cid, log_date=today_kst().isoformat(), kind="MEMO", content=content,
                              issue_class="product", created_by_name="직원1"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def account(self, company_id, login_id, must_change="N"):
        a = AsPortalAccount(company_id=company_id, login_id=login_id, hashed_password=hash_password(PW),
                            use_yn="Y", must_change_pw=must_change, token_ver=0)
        self.db.add(a)
        self.db.flush()
        return a

    def h(self, tok=None):
        return {"Authorization": f"Bearer {tok or self.tokA}"}

    def body(self, **kw):
        b = dict(reporter_name="홍길동", reporter_phone="010-1111-2222", method="CS", product_group="T10", qty=1,
                 symptom="화면이 안 나와요")
        b.update(kw)
        return b

    def raw_create(self, tok=None, **kw):
        return self.client.post("/api/portal/cases", json=self.body(**kw), headers=self.h(tok))

    def create(self, tok=None, **kw):
        r = self.raw_create(tok, **kw)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def photo(self, cid, tok=None, data=JPEG):
        return self.client.post(f"/api/portal/cases/{cid}/photos", headers=self.h(tok),
                                files={"file": ("a.jpg", data, "image/jpeg")})

    # ---- 로그인 ----
    def test_login_and_me(self):
        r = self.client.post("/api/portal/login", json={"login_id": " UserA ", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["account"], {"login_id": "usera", "company_name": "가고객", "must_change_pw": False})
        me = self.client.get("/api/portal/me", headers=self.h(j["token"]))
        self.assertEqual(me.json(), j["account"])
        self.assertEqual(self.client.post("/api/portal/login", json={"login_id": "usera", "password": "x"}).status_code, 401)
        self.assertEqual(self.client.get("/api/portal/me").status_code, 401)

    def test_login_rate_limited_before_password_check(self):
        for _ in range(LOGIN_WINDOW_MAX):
            check_login_rate()
        with mock.patch.object(portal_mod, "authenticate") as auth:
            r = self.client.post("/api/portal/login", json={"login_id": "usera", "password": PW})
            auth.assert_not_called()  # 막힌 요청은 bcrypt 를 하지 않는다
        self.assertEqual((r.status_code, r.json()["detail"]), (429, "잠시 후 다시 시도해 주세요"))

    def test_must_change_pw_blocks_everything_but_password(self):
        acc = self.account(1, "newuser", must_change="Y")
        self.db.commit()
        old = create_portal_token(acc)
        self.assertEqual(self.client.get("/api/portal/me", headers=self.h(old)).status_code, 200)
        for method, url in (("get", "/api/portal/cases"), ("get", "/api/portal/options"),
                            ("get", "/api/portal/cases/1"), ("post", "/api/portal/cases"),
                            ("get", "/api/portal/photos/1")):
            r = getattr(self.client, method)(url, headers=self.h(old))
            self.assertEqual(r.status_code, 403, url)
            self.assertEqual(r.json()["detail"], "비밀번호를 먼저 변경해 주세요")
        bad = self.client.post("/api/portal/password", json={"current": "nope", "new": "newpassword1"}, headers=self.h(old))
        self.assertEqual((bad.status_code, bad.json()["detail"]), (400, "현재 비밀번호가 올바르지 않습니다"))
        r = self.client.post("/api/portal/password", json={"current": PW, "new": "newpassword1"}, headers=self.h(old))
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()["token"]
        self.assertEqual(self.client.get("/api/portal/cases", headers=self.h(new)).status_code, 200)
        self.assertEqual(self.client.get("/api/portal/cases", headers=self.h(old)).status_code, 401)
        self.assertEqual(self.client.post("/api/portal/login", json={"login_id": "newuser", "password": "newpassword1"}).status_code, 200)

    # ---- 접수·격리 ----
    def test_create_and_list_own_only(self):
        c = self.create()
        self.assertEqual((c["status"], c["status_label"], c["product_group_name"]), ("received", "접수됨", "T10형"))
        self.assertRegex(c["as_no"], r"^AS\d{8}$")
        row = self.db.get(AsCase, c["id"])
        self.assertEqual((row.source, row.company_id, row.portal_account_id), ("portal", 1, self.accA.id))
        a = self.client.get("/api/portal/cases", headers=self.h()).json()
        self.assertEqual(len(a["rows"]), 1)
        self.assertFalse(a["has_more"])
        self.assertEqual(a["rows"][0]["symptom_head"], "화면이 안 나와요")
        self.assertEqual(a["rows"][0]["photo_count"], 0)
        self.assertEqual(self.client.get("/api/portal/cases", headers=self.h(self.tokB)).json()["rows"], [])

    def test_other_company_case_and_photo_are_404(self):
        cid = self.create()["id"]
        pid = self.photo(cid).json()["id"]
        hb = self.h(self.tokB)
        for method, url, kw in (
            ("get", f"/api/portal/cases/{cid}", {}),
            ("put", f"/api/portal/cases/{cid}", {"json": {"qty": 2}}),
            ("post", f"/api/portal/cases/{cid}/cancel", {}),
            ("post", f"/api/portal/cases/{cid}/photos", {"files": {"file": ("a.jpg", JPEG, "image/jpeg")}}),
            ("delete", f"/api/portal/cases/{cid}/photos/{pid}", {}),
            ("get", f"/api/portal/photos/{pid}", {}),
        ):
            r = getattr(self.client, method)(url, headers=hb, **kw)
            self.assertEqual(r.status_code, 404, url)
        self.assertEqual(self.client.get("/api/portal/cases/99999", headers=hb).json()["detail"], "접수 건을 찾을 수 없습니다")
        self.assertEqual(self.client.get("/api/portal/photos/99999", headers=hb).json()["detail"], "사진을 찾을 수 없습니다")
        self.assertEqual(self.db.get(AsCase, cid).status, "received")
        self.assertEqual(self.db.get(AsCase, cid).qty, 1)

    def test_internal_fields_never_in_responses(self):
        cid = self.create()["id"]
        secrets = {"action_note": "비밀조치XYZ", "delivery_note": "납품메모XYZ", "manager_name": "담당자XYZ",
                   "replaced_parts": "교체부품XYZ", "origin": "생산지XYZ", "part_no": "PARTXYZ", "item_name": "품목XYZ",
                   "rma_no_text": "RMAXYZ", "customer_notice": "고객안내OK"}
        row = self.db.get(AsCase, cid)   # 조치 내역은 직원도 고칠 수 없다(읽기 전용) → 저장값으로 직접 넣는다
        row.action_note = secrets["action_note"]
        self.db.commit()
        self.staff_save(cid, **{**secrets, "fault": "MAT"})
        texts = [self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).text,
                 self.client.get("/api/portal/cases", headers=self.h()).text]
        for t in texts:
            for k, v in secrets.items():
                if k != "customer_notice":
                    self.assertNotIn(v, t, k)
            self.assertNotIn("MAT", t)
        self.assertIn("고객안내OK", texts[0])
        detail = json.loads(texts[0])
        for k in ("action_note", "fault", "manager_name", "part_no", "company_id", "rev"):
            self.assertNotIn(k, detail)
        self.assertEqual(detail["status"], "progress")

    def test_staff_photo_hidden_from_customer(self):
        cid = self.create()["id"]
        spid = self.staff_photo(cid)
        self.assertEqual(self.client.get(f"/api/portal/photos/{spid}", headers=self.h()).status_code, 404)
        d = self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).json()
        self.assertEqual(d["photos"], [])
        self.assertEqual(self.client.delete(f"/api/portal/cases/{cid}/photos/{spid}", headers=self.h()).status_code, 404)
        mine = self.photo(cid).json()["id"]
        g = self.client.get(f"/api/portal/photos/{mine}", headers=self.h())
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.content, JPEG)
        self.assertEqual(g.headers["x-content-type-options"], "nosniff")
        self.assertEqual(g.headers["content-type"], "image/jpeg")
        self.assertNotIn("content-disposition", g.headers)
        lst = self.client.get("/api/portal/cases", headers=self.h()).json()["rows"][0]
        self.assertEqual(lst["photo_count"], 1)  # 직원 사진은 세지 않는다

    # ---- 수정·취소 ----
    def test_edit_and_cancel_only_when_received(self):
        cid = self.create()["id"]
        r = self.client.put(f"/api/portal/cases/{cid}", json={"qty": 3, "symptom": "고침", "product_group": "K13",
                                                              "action_note": "해킹", "part_no": "HACK"}, headers=self.h())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["qty"], r.json()["symptom"], r.json()["product_group_name"]), (3, "고침", "K13형"))
        row = self.db.get(AsCase, cid)
        self.assertIsNone(row.action_note)
        self.assertIsNone(row.part_no)
        self.staff_save(cid, part_no="KJC-1")
        self.assertEqual(self.db.get(AsCase, cid).status, "open")
        r = self.client.put(f"/api/portal/cases/{cid}", json={"qty": 4}, headers=self.h())
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "이미 확인된 접수입니다. 변경은 담당자에게 문의해 주세요"))
        self.assertEqual(self.client.post(f"/api/portal/cases/{cid}/cancel", headers=self.h()).status_code, 409)
        d = self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).json()
        self.assertEqual((d["status"], d["can_edit"]), ("progress", False))

    def test_cancel_marks_closed_with_note(self):
        cid = self.create()["id"]
        self.staff_save(cid, manager_name="김담당")   # 직원 저장 1회(수정 번호 1)
        self.db.get(AsCase, cid).action_note = "기존 메모"
        self.db.commit()
        r = self.client.post(f"/api/portal/cases/{cid}/cancel", headers=self.h())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], r.json()["status_label"]), ("canceled", "취소됨"))
        self.assertEqual((r.json()["can_edit"], r.json()["can_add_photo"]), (False, False))
        listed = self.client.get("/api/portal/cases?filter=done", headers=self.h()).json()["rows"][0]
        self.assertEqual((listed["status"], listed["status_label"]), ("canceled", "취소됨"))
        row = self.db.get(AsCase, cid)
        self.assertEqual(row.status, "closed")
        self.assertIsNotNone(row.closed_at)
        self.assertIsNotNone(row.canceled_at)
        self.assertEqual(row.action_note, "기존 메모")   # 취소는 처리 기록 줄로 남기고 조치 내역은 건드리지 않는다
        rev = self.db.query(RecordRevision).filter_by(scope="as.case", ref_key=str(cid)).one()
        self.assertEqual((rev.rev, rev.updated_by_name), (2, "고객:홍길동"))  # 직원 저장 1 + 취소 1

    def test_cancel_adds_log_line_not_action_note(self):
        cid = self.create()["id"]
        self.client.post(f"/api/portal/cases/{cid}/cancel", headers=self.h())
        self.db.expire_all()
        row = self.db.get(AsCase, cid)
        self.assertIsNone(row.action_note)
        self.assertEqual(len(row.logs), 1)
        lg = row.logs[0]
        self.assertEqual((lg.kind, lg.log_date, lg.created_by_name, lg.content),
                         ("CANCEL", today_kst().isoformat(), "고객:홍길동", "고객이 접수를 취소했습니다 (홍길동)"))

    def test_logs_never_in_portal_responses(self):
        cid = self.create()["id"]
        self.staff_log(cid, "비밀기록XYZ")
        for url in (f"/api/portal/cases/{cid}", "/api/portal/cases", "/api/portal/cases?filter=done"):
            body = self.client.get(url, headers=self.h())
            self.assertEqual(body.status_code, 200, url)
            self.assertNotIn("비밀기록XYZ", body.text)
            self.assertNotIn('"logs"', body.text)

    def _confirm_during_lock(self):
        """수정 번호 잠금을 잡는 사이 직원이 확인(open)해 커밋한 상황을 흉내 낸다."""
        real = portal_mod._bump_case_rev
        other = sessionmaker(bind=self.db.get_bind())()

        def bump(db, case_id, reporter_name):
            real(db, case_id, reporter_name)
            other.query(AsCase).filter_by(id=case_id).update({"status": "open", "part_no": "KJC-1"},
                                                            synchronize_session=False)
            other.commit()

        self.addCleanup(other.close)
        return mock.patch.object(portal_mod, "_bump_case_rev", bump)

    def test_edit_after_staff_confirm_in_between_is_409(self):
        cid = self.create(qty=1)["id"]
        with self._confirm_during_lock():
            r = self.client.put(f"/api/portal/cases/{cid}", json={"qty": 7, "symptom": "덮어쓰기"}, headers=self.h())
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "이미 확인된 접수입니다. 변경은 담당자에게 문의해 주세요"))
        self.db.expire_all()
        row = self.db.get(AsCase, cid)
        self.assertEqual((row.status, row.qty, row.symptom), ("open", 1, "화면이 안 나와요"))

    def test_staff_close_shows_done_and_reopen_clears_customer_cancel(self):
        # 직원이 닫은 건(자체클로징)은 고객에게 "완료", 고객이 취소한 건만 "취소됨"
        a = self.create()["id"]
        self.staff_status(a, "close")
        v = self.client.get(f"/api/portal/cases/{a}", headers=self.h()).json()
        self.assertEqual((v["status"], v["status_label"]), ("done", "완료"))
        # 고객 취소 → 직원이 되돌렸다가 다시 닫으면 더는 "취소됨"이 아니다
        b = self.create()["id"]
        self.client.post(f"/api/portal/cases/{b}/cancel", headers=self.h())
        self.staff_status(b, "reopen")
        self.assertIsNone(self.db.get(AsCase, b).canceled_at)
        self.assertEqual(self.client.get(f"/api/portal/cases/{b}", headers=self.h()).json()["status"], "progress")
        self.staff_status(b, "close")
        self.assertEqual(self.client.get(f"/api/portal/cases/{b}", headers=self.h()).json()["status"], "done")
        # 직원 화면이 읽는 고객 취소 시각은 고객이 취소한 건에만 남는다
        c = self.create()["id"]
        self.client.post(f"/api/portal/cases/{c}/cancel", headers=self.h())
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(AsCase, c).canceled_at)
        self.assertIsNone(self.db.get(AsCase, a).canceled_at)

    def test_cancel_after_staff_confirm_in_between_is_409(self):
        cid = self.create()["id"]
        with self._confirm_during_lock():
            r = self.client.post(f"/api/portal/cases/{cid}/cancel", headers=self.h())
        self.assertEqual(r.status_code, 409)
        self.db.expire_all()
        row = self.db.get(AsCase, cid)
        self.assertEqual(row.status, "open")
        self.assertIsNone(row.closed_at)
        self.assertIsNone(row.action_note)

    # ---- 사진 ----
    def test_photo_add_allowed_until_done(self):
        cid = self.create()["id"]
        self.staff_save(cid, part_no="KJC-1")
        self.assertEqual(self.photo(cid).status_code, 200)  # open
        self.staff_status(cid, "done")
        self.assertEqual(self.photo(cid).status_code, 409)
        self.assertEqual(self.photo(cid, data=b"not an image").status_code, 409)

    def test_photo_delete_only_received_and_own(self):
        cid = self.create()["id"]
        pid = self.photo(cid).json()["id"]
        self.assertEqual(self.photo(cid, data=b"GIF89a....").status_code, 400)
        self.staff_save(cid, part_no="KJC-1")
        self.assertEqual(self.client.delete(f"/api/portal/cases/{cid}/photos/{pid}", headers=self.h()).status_code, 409)
        c2 = self.create(client_key="k2")["id"]
        p2 = self.photo(c2).json()["id"]
        self.assertEqual(self.client.delete(f"/api/portal/cases/{c2}/photos/{p2}", headers=self.h()).status_code, 200)
        self.assertEqual(self.db.query(AsCasePhoto).filter_by(id=p2).count(), 0)

    # ---- 두 번 접수·길이·코드 ----
    def test_same_client_key_returns_same_case(self):
        a = self.create(client_key="k1")
        b = self.create(client_key="k1")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(self.db.query(AsCase).count(), 1)
        c = self.create(tok=self.tokB, client_key="k1")  # 다른 계정은 같은 키여도 별개
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(self.raw_create(client_key="k" * 41).status_code, 400)

    def test_client_key_of_moved_case_is_not_leaked(self):
        cid = self.create(client_key="moved")["id"]
        self.staff_save(cid, company_id=2, customer_notice="B전용안내")
        r = self.raw_create(client_key="moved")
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "이미 처리된 접수 요청입니다. 접수 목록을 확인해 주세요"))
        self.assertNotIn("B전용안내", r.text)
        self.assertEqual(self.db.query(AsCase).count(), 1)
        # 제약 위반 경로(첫 조회를 놓친 동시 요청)에서도 500 이 아니라 같은 409
        from app.routers import portal as as_portal
        real, calls = as_portal._existing_by_key, {"n": 0}

        def fake(db, account, key):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(db, account, key)

        with mock.patch.object(as_portal, "_existing_by_key", fake):
            r = self.raw_create(client_key="moved")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertNotIn("B전용안내", r.text)

    def test_client_key_race_returns_existing(self):
        first = self.create(client_key="race")
        from app.routers import portal as as_portal
        calls = {"n": 0}
        real = as_portal._existing_by_key

        def fake(db, account, key):  # 첫 확인에서는 못 찾고(동시 요청), 제약 위반 뒤에는 찾는다
            calls["n"] += 1
            return None if calls["n"] == 1 else real(db, account, key)

        with mock.patch.object(as_portal, "_existing_by_key", fake):
            r = self.raw_create(client_key="race")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], first["id"])
        self.assertEqual(self.db.query(AsCase).count(), 1)

    def test_symptom_length_and_unicode(self):
        s = "화면이 안 나와요 😢\n두번째 줄"
        self.assertEqual(self.create(symptom=s)["symptom"], s)
        r = self.raw_create(symptom="가" * 4001)
        self.assertEqual((r.status_code, r.json()["detail"]), (400, "증상은 4,000자까지 입력할 수 있습니다"))
        self.assertEqual(self.raw_create(symptom="가" * 4000, client_key="ok").status_code, 200)
        self.assertEqual(self.raw_create(reporter_name="가" * 61).status_code, 400)
        self.assertEqual(self.raw_create(model_text="a" * 201).status_code, 400)

    def test_validation(self):
        for kw in ({"qty": 0}, {"qty": 10000}, {"qty": "x"}, {"qty": 1.5}, {"qty": True}, {"qty": None}):
            r = self.raw_create(**kw)
            self.assertEqual((r.status_code, r.json()["detail"]), (400, "수량을 확인해 주세요"), kw)
        self.assertEqual(self.create(qty=9999)["qty"], 9999)
        for drop in ("reporter_name", "reporter_phone", "method", "product_group", "symptom", "qty"):
            b = self.body()
            b.pop(drop)
            self.assertEqual(self.client.post("/api/portal/cases", json=b, headers=self.h()).status_code, 400, drop)
        self.assertEqual(self.raw_create(reporter_name="  ").status_code, 400)
        self.assertEqual(self.raw_create(reporter_name=123).status_code, 400)

    def test_unknown_product_group_400(self):
        self.assertEqual(self.raw_create(product_group="ZZZ").status_code, 400)
        cid = self.create()["id"]
        self.assertEqual(self.client.put(f"/api/portal/cases/{cid}", json={"product_group": "ZZZ"}, headers=self.h()).status_code, 400)
        self.assertEqual(self.db.get(AsCase, cid).product_group, "T10")

    # ---- 직원 연동 ----
    def test_company_change_moves_visibility(self):
        cid = self.create()["id"]
        self.staff_save(cid, company_id=2)
        self.assertEqual(self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).status_code, 404)
        self.assertEqual(self.client.get(f"/api/portal/cases/{cid}", headers=self.h(self.tokB)).status_code, 200)

    def test_customer_edit_bumps_revision(self):
        cid = self.create()["id"]
        # 직원 화면은 저장할 때 수정 번호가 그대로인지 본다 → 고객이 고치면 번호가 올라가야 직원 쪽이 충돌로 잡는다
        self.staff_save(cid, manager_name="김담당")          # 직원이 본 번호 = 1
        self.assertEqual(self.client.put(f"/api/portal/cases/{cid}", json={"qty": 2}, headers=self.h()).status_code, 200)
        self.db.expire_all()
        rev = self.db.get(RecordRevision, ("as.case", str(cid)))
        self.assertEqual((rev.rev, rev.updated_by_name), (2, "고객:홍길동"))

    def test_token_separation(self):
        # 직원(CRM) 모양의 토큰은 이 서버의 키로 서명돼 있어도 고객 API 에 통하지 않는다
        from jose import jwt
        from app.services import portal_auth
        staff = jwt.encode({"sub": "t1", "exp": datetime(2099, 1, 1, tzinfo=timezone.utc)},
                           portal_auth.SECRET_KEY, algorithm="HS256")
        self.assertEqual(self.client.get("/api/portal/cases", headers={"Authorization": f"Bearer {staff}"}).status_code, 401)
        self.assertEqual(self.client.get("/api/portal/me", headers={"Authorization": f"Bearer {staff}"}).status_code, 401)

    def test_deactivated_account_rejected(self):
        self.accA.use_yn = "N"
        self.db.commit()
        self.assertEqual(self.client.get("/api/portal/me", headers=self.h()).status_code, 401)

    # ---- 목록·옵션 ----
    def test_options_and_recent_reporters_dedup_limit_5(self):
        for i in range(7):
            self.create(reporter_name=f"접수자{i}", reporter_phone="010", client_key=f"k{i}")
        self.create(reporter_name="접수자6", reporter_phone="010", client_key="dup")  # 중복
        self.create(tok=self.tokB, reporter_name="남의접수자", reporter_phone="999")
        j = self.client.get("/api/portal/options", headers=self.h()).json()
        self.assertEqual(j["product_groups"], [{"code": "T10", "name": "T10형"}, {"code": "K13", "name": "K13형"}])
        self.assertEqual([r["name"] for r in j["recent_reporters"]],
                         ["접수자6", "접수자5", "접수자4", "접수자3", "접수자2"])
        self.assertEqual(j["recent_reporters"][0], {"name": "접수자6", "phone": "010"})

    def test_list_filter_and_paging(self):
        ids = [self.create(client_key=f"p{i}")["id"] for i in range(23)]
        p1 = self.client.get("/api/portal/cases", headers=self.h()).json()
        self.assertEqual((len(p1["rows"]), p1["has_more"]), (20, True))
        self.assertEqual(p1["rows"][0]["id"], ids[-1])  # 최신 먼저
        p2 = self.client.get("/api/portal/cases?offset=20", headers=self.h()).json()
        self.assertEqual((len(p2["rows"]), p2["has_more"]), (3, False))
        self.client.post(f"/api/portal/cases/{ids[0]}/cancel", headers=self.h())
        self.staff_save(ids[1], part_no="P")
        done = self.client.get("/api/portal/cases?filter=done", headers=self.h()).json()["rows"]
        self.assertEqual([r["id"] for r in done], [ids[0]])
        prog = self.client.get("/api/portal/cases?filter=progress&offset=2", headers=self.h()).json()
        self.assertEqual((len(prog["rows"]), prog["has_more"]), (20, False))  # 취소 1건만 빠진 22건 중 2건 건너뜀
        self.assertEqual(self.client.get("/api/portal/cases?filter=zzz", headers=self.h()).status_code, 400)


    # ---- 사진 폴더(NAS)가 끊겼을 때 ----
    def test_photo_upload_when_storage_is_down(self):
        cid = self.create()["id"]
        blocker = self.tmp / "not-a-folder"
        blocker.write_bytes(b"x")
        with mock.patch.object(photo_mod, "AS_UPLOAD_DIR", blocker / "as"), self.assertLogs(photo_mod.log, "ERROR"):
            r = self.photo(cid)
        self.assertEqual(r.status_code, 503)
        self.assertIn("잠시 후", r.json()["detail"])
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)
        d = self.client.get(f"/api/portal/cases/{cid}", headers=self.h())
        self.assertEqual((d.status_code, d.json()["status"], d.json()["photos"]), (200, "received", []))
        self.assertEqual(self.photo(cid).status_code, 200)   # 폴더가 돌아오면 다시 올릴 수 있다

    def test_health_needs_no_login(self):
        self.assertEqual(self.client.get("/api/portal/health").json(), {"status": "ok"})


    # ---- 접수 구분 (현장 / 입고 수리) — 고객이 접수할 때 고른다. 직원 화면의 '구분'(as_case.method)과 같은 칸 ----
    def test_kind_is_required_and_saved_to_staff_field(self):
        for bad, msg in ((None, "접수 구분을 선택해 주세요"), ("", "접수 구분을 선택해 주세요"), ("  ", "접수 구분을 선택해 주세요"),
                         ("ZZ", "접수 구분을 확인해 주세요"), (1, "접수 구분을 선택해 주세요")):
            r = self.raw_create(method=bad)
            self.assertEqual((r.status_code, r.json()["detail"]), (400, msg), bad)
        self.assertEqual(self.db.query(AsCase).count(), 0)
        c = self.create(method="AS")
        self.assertEqual((c["method"], c["method_name"]), ("AS", "AS (입고 수리)"))
        self.assertEqual(self.db.get(AsCase, c["id"]).method, "AS")       # 직원 화면이 읽는 칸에 그대로 들어간다
        d = self.client.get(f"/api/portal/cases/{c['id']}", headers=self.h()).json()
        self.assertEqual((d["method"], d["method_name"]), ("AS", "AS (입고 수리)"))

    def test_kind_options_listed(self):
        j = self.client.get("/api/portal/options", headers=self.h()).json()
        self.assertEqual(j["kinds"], [{"code": "CS", "name": "CS (현장)"}, {"code": "AS", "name": "AS (입고 수리)"}])

    def test_unused_kind_is_not_offered_or_accepted(self):
        code = self.db.query(CommonCode).filter_by(code="AS").one()
        code.use_yn = "N"
        self.db.commit()
        j = self.client.get("/api/portal/options", headers=self.h()).json()
        self.assertEqual([k["code"] for k in j["kinds"]], ["CS"])
        self.assertEqual(self.raw_create(method="AS").status_code, 400)

    def test_kind_can_be_changed_while_received_only(self):
        cid = self.create(method="CS")["id"]
        r = self.client.put(f"/api/portal/cases/{cid}", json={"method": "AS"}, headers=self.h())
        self.assertEqual((r.status_code, r.json()["method"]), (200, "AS"))
        self.assertEqual(self.client.put(f"/api/portal/cases/{cid}", json={"method": "ZZ"}, headers=self.h()).status_code, 400)
        self.assertEqual(self.client.put(f"/api/portal/cases/{cid}", json={"method": ""}, headers=self.h()).status_code, 400)
        self.assertEqual(self.client.put(f"/api/portal/cases/{cid}", json={"qty": 2}, headers=self.h()).status_code, 200)
        self.assertEqual(self.db.get(AsCase, cid).method, "AS")           # 키가 없으면 건드리지 않는다
        self.staff_save(cid, part_no="KJC-1", method="CS")                # 직원이 확인하며 구분을 고침
        r = self.client.put(f"/api/portal/cases/{cid}", json={"method": "AS"}, headers=self.h())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.db.get(AsCase, cid).method, "CS")
        self.assertEqual(self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).json()["method"], "CS")

    def test_old_case_without_kind_still_opens(self):
        # 이 칸이 생기기 전에 접수된 건(구분 없음)도 목록·상세가 열린다
        cid = self.create()["id"]
        row = self.db.get(AsCase, cid)
        row.method = None
        self.db.commit()
        d = self.client.get(f"/api/portal/cases/{cid}", headers=self.h()).json()
        self.assertEqual((d["method"], d["method_name"]), (None, None))
        self.assertEqual(self.client.get("/api/portal/cases", headers=self.h()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
