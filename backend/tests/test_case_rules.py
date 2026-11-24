"""고객용 상태·권한·응답 변환과 입력 도우미 검증 (DB 는 메모리 SQLite)."""
import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AsCase, CommonCode, CommonCodeGroup
from app.services.case_rules import (
    CUSTOMER_STATUS_LABEL, PORTAL_FIELDS, REV_SCOPE, SYMPTOM_MAX, check_text_lengths, code_names,
    customer_can_add_photo, customer_can_edit, customer_status, portal_view, today_kst,
)

STATUSES = ("received", "open", "hold", "done", "closed")
ALL_COLUMN_NAMES = [c.name for c in AsCase.__table__.columns]


class CaseRulesTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual((REV_SCOPE, SYMPTOM_MAX), ("as.case", 4000))
        self.assertEqual(CUSTOMER_STATUS_LABEL,
                         {"received": "접수됨", "progress": "처리 중", "done": "완료", "canceled": "취소됨"})

    def test_customer_status(self):
        self.assertEqual([customer_status(s) for s in STATUSES], ["received", "progress", "progress", "done", "done"])
        # 고객이 직접 취소해 닫힌 건만 "취소됨" — 다른 상태는 취소 표시가 있어도 그대로
        self.assertEqual([customer_status(s, True) for s in STATUSES],
                         ["received", "progress", "progress", "done", "canceled"])

    def test_customer_permissions(self):
        self.assertEqual([customer_can_edit(s) for s in STATUSES], [True, False, False, False, False])
        self.assertEqual([customer_can_add_photo(s) for s in STATUSES], [True, True, True, False, False])

    def test_portal_view_never_leaks_internal_fields(self):
        attrs = {c: f"v-{c}" for c in ALL_COLUMN_NAMES}
        attrs.update(status="hold", qty=1, canceled_at=None)
        case = SimpleNamespace(**attrs)
        photos = [SimpleNamespace(id=1, orig_name="a.jpg", uploaded_by="customer"),
                  SimpleNamespace(id=2, orig_name="b.jpg", uploaded_by="staff")]
        v = portal_view(case, photos)
        for k in ("action_note", "delivery_note", "manager_name", "replaced_parts", "origin", "fault",
                  "rma_id", "rma_no_text", "part_no", "item_name", "company_id", "portal_account_id"):
            self.assertNotIn(k, v)
        self.assertEqual((v["status"], v["status_label"], v["can_edit"], v["can_add_photo"]),
                         ("progress", "처리 중", False, True))
        self.assertEqual(v["photos"], [{"id": 1, "orig_name": "a.jpg", "kind": "photo"}])
        for f in PORTAL_FIELDS:
            self.assertIn(f, v)

    def test_today_kst_is_a_date(self):
        self.assertIsInstance(today_kst(), date)

    def test_check_text_lengths(self):
        check_text_lengths({"reporter_name": "가" * 60, "site_name": None}, ("reporter_name", "site_name"))
        with self.assertRaises(HTTPException) as cm:
            check_text_lengths({"reporter_name": "가" * 61}, ("reporter_name",))
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (400, "접수자는 60자까지 입력할 수 있습니다"))

    def test_code_names(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        g = CommonCodeGroup(group_code="AS_PRODUCT_GROUP", group_name="제품군")
        other = CommonCodeGroup(group_code="AS_KIND", group_name="구분")
        db.add_all([g, other])
        db.flush()
        db.add_all([CommonCode(group_id=g.id, code="T10", display_name="T10형"),
                    CommonCode(group_id=other.id, code="CS", display_name="CS (현장)")])
        db.commit()
        self.assertEqual(code_names(db, "AS_PRODUCT_GROUP"), {"T10": "T10형"})
        self.assertEqual(code_names(db, "NOPE"), {})


if __name__ == "__main__":
    unittest.main()
