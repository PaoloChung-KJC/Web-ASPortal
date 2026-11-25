"""영상 받기 · 1분 구간 자르기 검증: 메모리 SQLite + 임시 폴더. ffmpeg 는 실제로 돌리지 않는다(_run 을 바꿔 끼운다).

2026-11-25: FHD 다시 압축(변환)은 뺐다. 1분이 넘는 영상은 화면에서 구간을 고르고, 서버는 ffmpeg 로 그 구간만
다시 압축하지 않고(-c copy) 떼어 낸다. 1분 이하는 원본 그대로.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import AsCase, AsCasePhoto, AsPortalAccount, Company
from app.routers.portal import router
from app.services import photo, video
from app.services.case_rules import portal_view
from app.services.portal_auth import create_portal_token, hash_password

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 200
GP3 = b"\x00\x00\x00\x18ftyp3gp4" + b"\x00" * 200
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
TRIMMED = b"\x00\x00\x00\x14ftypqt  " + b"-trimmed"


def fake_trim_ok(cmd, timeout):
    Path(cmd[-1]).write_bytes(TRIMMED)
    return True


class VideoServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.tmp = Path(tempfile.mkdtemp())
        self.up = self.tmp / "as"
        self.work = self.tmp / "work"
        for target, name, value in ((photo, "AS_UPLOAD_DIR", self.up), (video, "AS_UPLOAD_DIR", self.up),
                                    (video, "VIDEO_WORK_DIR", self.work), (video, "FFMPEG_PATH", "ffmpeg.exe")):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.db.add(Company(id=1, name="가고객"))
        self.case = AsCase(as_no="AS99120001", company_id=1, received_date="2099-12-31", symptom="x")
        self.db.add(self.case)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload_tmp(self, data=MP4):
        self.work.mkdir(parents=True, exist_ok=True)
        f = self.work / f"up-{len(list(self.work.iterdir()))}"
        f.write_bytes(data)
        return f

    def store(self, data=MP4, name="a.mov"):
        return video.store_original(self.db, self.case, self.upload_tmp(data), name, "customer", "고객:홍길동")

    # ---- 형식 ----
    def test_sniff_video(self):
        self.assertEqual(video.sniff_video(MP4), ("video/mp4", ".mp4"))
        self.assertEqual(video.sniff_video(MOV), ("video/quicktime", ".mov"))
        self.assertEqual(video.sniff_video(GP3), ("video/3gpp", ".3gp"))
        self.assertEqual(video.sniff_video(WEBM), ("video/webm", ".webm"))
        self.assertIsNone(video.sniff_video(HEIC))    # 아이폰 사진(HEIC)도 ftyp 로 시작한다 — 영상이 아니다
        self.assertIsNone(video.sniff_video(JPEG))
        self.assertIsNone(video.sniff_video(b""))

    # ---- 원본 저장 ----
    def test_store_original_saves_file_and_row(self):
        p = self.store(MOV, "IMG_0001.MOV")
        self.assertRegex(p.stored_name, r"^[0-9a-f]{32}\.mov$")
        self.assertEqual((p.content_type, p.size, p.uploaded_by, p.orig_name),
                         ("video/quicktime", len(MOV), "customer", "IMG_0001.MOV"))
        self.assertEqual(photo.photo_path(p).read_bytes(), MOV)
        self.assertEqual(photo.photo_path(p).parent, self.up / str(self.case.id))

    def test_rejects_non_video_and_oversize(self):
        with self.assertRaises(HTTPException) as cm:
            self.store(JPEG)
        self.assertEqual(cm.exception.status_code, 400)
        with mock.patch.object(video, "VIDEO_MAX_BYTES", 100), self.assertRaises(HTTPException) as cm:
            self.store(MP4)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("영상", cm.exception.detail)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)

    def test_video_limit_three_and_counts_apart_from_photos(self):
        for _ in range(photo.CUSTOMER_MAX):
            photo.save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        for _ in range(video.CUSTOMER_VIDEO_MAX):
            self.store()
        with self.assertRaises(HTTPException) as cm:
            self.store()
        self.assertIn("최대 3개", cm.exception.detail)
        self.assertEqual(self.db.query(AsCasePhoto).count(), photo.CUSTOMER_MAX + video.CUSTOMER_VIDEO_MAX)

    def test_photo_limit_ignores_videos(self):
        for _ in range(video.CUSTOMER_VIDEO_MAX):
            self.store()
        for _ in range(photo.CUSTOMER_MAX):
            photo.save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        with self.assertRaises(HTTPException):
            photo.save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")

    def test_store_write_failure_is_503(self):
        with mock.patch.object(video.shutil, "copyfile", side_effect=OSError("nas down")), \
                self.assertLogs(video.log, "ERROR"), self.assertRaises(HTTPException) as cm:
            self.store()
        self.assertEqual(cm.exception.status_code, 503)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)

    # ---- 자르기 ----
    def test_trim_command_copies_without_reencoding(self):
        cmd = video.trim_cmd("ffmpeg.exe", Path("in.mov"), Path("out.mov"), 35.5, 60)
        self.assertEqual((cmd[0], cmd[-1]), ("ffmpeg.exe", "out.mov"))
        self.assertEqual(cmd[cmd.index("-ss") + 1], "35.500")
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))          # 입력 앞 -ss: 빠르게 그 자리로
        self.assertEqual(cmd[cmd.index("-t") + 1], "60.000")
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")          # 다시 압축하지 않는다 — 화질 그대로
        self.assertNotIn("libx264", cmd)
        self.assertIn("+faststart", cmd)
        webm = video.trim_cmd("ffmpeg.exe", Path("in.webm"), Path("out.webm"), 0, 60)
        self.assertNotIn("-movflags", webm)                           # webm 에는 없는 옵션

    def test_trim_length_is_capped_at_one_minute(self):
        self.assertEqual(video.trim_span(10, 200), (10.0, 60.0))
        self.assertEqual(video.trim_span(10, 40), (10.0, 30.0))
        self.assertEqual(video.trim_span(-5, None), (0.0, 60.0))
        self.assertEqual(video.trim_span(30, 30), (30.0, 60.0))   # 끝이 시작보다 앞이면 1분

    def test_trim_returns_new_file_and_keeps_extension(self):
        src = self.upload_tmp(MOV)
        with mock.patch.object(video, "_run", side_effect=fake_trim_ok) as run:
            out = video.trim(src, 35, 95, ".mov")
        self.assertEqual(out.read_bytes(), TRIMMED)
        self.assertEqual(out.suffix, ".mov")
        self.assertEqual(run.call_args[0][0][-1], str(out))

    def test_trim_failure_or_no_ffmpeg_returns_none(self):
        src = self.upload_tmp(MOV)
        with mock.patch.object(video, "_run", return_value=False):
            self.assertIsNone(video.trim(src, 0, 60, ".mov"))
        with mock.patch.object(video, "FFMPEG_PATH", ""), mock.patch.object(video, "_run") as run:
            self.assertIsNone(video.trim(src, 0, 60, ".mov"))
        run.assert_not_called()
        self.assertEqual([p.name for p in self.work.iterdir()], [src.name])   # 실패해도 결과 조각을 남기지 않는다

    def test_portal_view_marks_kind(self):
        photo.save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        self.store()
        self.db.commit()
        self.db.refresh(self.case)
        kinds = [p["kind"] for p in portal_view(self.case, self.case.photos)["photos"]]
        self.assertEqual(kinds, ["photo", "video"])


class VideoApiTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Company(id=1, name="가고객"))
        acc = AsPortalAccount(company_id=1, login_id="usera", hashed_password=hash_password("password1"),
                              use_yn="Y", must_change_pw="N", token_ver=0)
        self.db.add(acc)
        self.case = AsCase(as_no="AS99120001", company_id=1, received_date="2099-12-31", symptom="x", status="received")
        self.db.add(self.case)
        self.db.commit()
        self.tmp = Path(tempfile.mkdtemp())
        self.work = self.tmp / "work"
        for target, name, value in ((photo, "AS_UPLOAD_DIR", self.tmp / "as"), (video, "AS_UPLOAD_DIR", self.tmp / "as"),
                                    (video, "VIDEO_WORK_DIR", self.work), (video, "FFMPEG_PATH", "ffmpeg.exe")):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.h = {"Authorization": f"Bearer {create_portal_token(acc)}"}

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def up(self, data, name="v.mov", ctype="video/quicktime", **form):
        return self.client.post(f"/api/portal/cases/{self.case.id}/photos", headers=self.h,
                                files={"file": (name, data, ctype)}, data={k: str(v) for k, v in form.items()})

    def stored(self, pid):
        return photo.photo_path(self.db.get(AsCasePhoto, pid)).read_bytes()

    def assert_no_work_files(self):
        self.assertEqual(list(self.work.iterdir()) if self.work.exists() else [], [])

    def test_short_video_is_stored_as_is_without_ffmpeg(self):
        with mock.patch.object(video, "_run") as run:
            r = self.up(MOV)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kind"], "video")
        run.assert_not_called()
        self.assertEqual(self.stored(r.json()["id"]), MOV)
        self.assert_no_work_files()
        d = self.client.get(f"/api/portal/cases/{self.case.id}", headers=self.h).json()
        self.assertEqual([p["kind"] for p in d["photos"]], ["video"])
        g = self.client.get(f"/api/portal/photos/{r.json()['id']}", headers=self.h)
        self.assertEqual((g.status_code, g.headers["content-type"]), (200, "video/quicktime"))

    def test_long_video_is_trimmed_to_chosen_minute(self):
        with mock.patch.object(video, "_run", side_effect=fake_trim_ok) as run:
            r = self.up(MOV, trim_start=35.2, trim_end=95.2)
        self.assertEqual(r.status_code, 200, r.text)
        cmd = run.call_args[0][0]
        self.assertEqual((cmd[cmd.index("-ss") + 1], cmd[cmd.index("-t") + 1]), ("35.200", "60.000"))
        p = self.db.get(AsCasePhoto, r.json()["id"])
        self.assertEqual((self.stored(p.id), p.size, p.content_type, p.orig_name),
                         (TRIMMED, len(TRIMMED), "video/quicktime", "v.mov"))
        self.assert_no_work_files()

    def test_trim_failure_keeps_whole_original(self):
        with mock.patch.object(video, "_run", return_value=False):
            r = self.up(MOV, trim_start=0)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.stored(r.json()["id"]), MOV)
        self.assert_no_work_files()

    def test_bad_trim_values_are_400(self):
        for bad in ("abc", "nan", "-inf"):
            r = self.up(MOV, trim_start=bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)
        self.assert_no_work_files()

    def test_upload_photo_still_works(self):
        r = self.up(JPEG, "a.jpg", "image/jpeg")
        self.assertEqual((r.status_code, r.json()["kind"]), (200, "photo"))

    def test_upload_rejects_other_files(self):
        r = self.up(b"MZ" + b"0" * 100, "x.exe", "application/octet-stream")
        self.assertEqual(r.status_code, 400)
        self.assertIn("영상", r.json()["detail"])

    def test_upload_oversize_video_is_400_and_leaves_no_work_file(self):
        with mock.patch.object(video, "VIDEO_MAX_BYTES", 100):
            r = self.up(MOV)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)
        self.assert_no_work_files()


if __name__ == "__main__":
    unittest.main()
