"""영상 받기·FHD 변환 검증: 메모리 SQLite + 임시 폴더. ffmpeg 는 실제로 돌리지 않는다(_run 을 바꿔 끼운다)."""
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
CONVERTED = b"\x00\x00\x00\x20ftypisom-converted"


def fake_run_ok(cmd, timeout):
    Path(cmd[-1]).write_bytes(CONVERTED)
    return True


class VideoServiceTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.tmp = Path(tempfile.mkdtemp())
        self.up = self.tmp / "as"
        self.work = self.tmp / "work"
        for target, value in ((photo, self.up), (video, self.up)):
            p = mock.patch.object(target, "AS_UPLOAD_DIR", value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("VIDEO_WORK_DIR", self.work), ("FFMPEG_PATH", "ffmpeg.exe")):
            p = mock.patch.object(video, name, value)
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

    def test_ffmpeg_command_limits_to_fhd_and_strips_metadata(self):
        cmd = video.ffmpeg_cmd("ffmpeg.exe", Path("in.mov"), Path("out.mp4"))
        self.assertEqual((cmd[0], cmd[-1]), ("ffmpeg.exe", "out.mp4"))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("1920", vf)
        self.assertIn("1080", vf)
        self.assertIn("force_original_aspect_ratio=decrease", vf)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
        self.assertEqual(cmd[cmd.index("-fpsmax") + 1], "30")
        self.assertEqual(cmd[cmd.index("-map_metadata") + 1], "-1")   # 촬영 위치(GPS) 등은 남기지 않는다
        self.assertIn("+faststart", cmd)

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
        # 영상이 있어도 사진 한도는 사진끼리만 센다
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

    # ---- 변환 ----
    def stored_and_committed(self, data=MOV):
        src = self.upload_tmp(data)
        p = video.store_original(self.db, self.case, src, "a.mov", "customer", "고객:홍길동")
        self.db.commit()
        return p.id, photo.photo_path(p), src

    def test_convert_replaces_original_with_fhd_mp4(self):
        pid, orig_path, src = self.stored_and_committed()
        with mock.patch.object(video, "_run", side_effect=fake_run_ok):
            video.convert_video(pid, src, self.Session)
        self.db.expire_all()
        self.assertIsNone(self.db.get(AsCasePhoto, pid))
        rows = self.db.query(AsCasePhoto).all()
        self.assertEqual(len(rows), 1)
        new = rows[0]
        self.assertEqual((new.content_type, new.size, new.orig_name, new.uploaded_by, new.uploaded_by_name),
                         ("video/mp4", len(CONVERTED), "a.mov", "customer", "고객:홍길동"))
        self.assertTrue(new.stored_name.endswith(".mp4"))
        self.assertEqual(photo.photo_path(new).read_bytes(), CONVERTED)
        self.assertFalse(orig_path.exists())                       # 원본은 지운다(사용자 결정)
        self.assertFalse((self.up / "_trash" / orig_path.name).exists())
        self.assertEqual(list(self.work.iterdir()), [])            # 작업 파일도 남기지 않는다

    def test_convert_failure_keeps_original(self):
        pid, orig_path, src = self.stored_and_committed()
        with mock.patch.object(video, "_run", return_value=False):
            video.convert_video(pid, src, self.Session)
        self.db.expire_all()
        p = self.db.get(AsCasePhoto, pid)
        self.assertEqual(p.content_type, "video/quicktime")
        self.assertTrue(orig_path.exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_without_ffmpeg_keeps_original(self):
        pid, orig_path, src = self.stored_and_committed()
        with mock.patch.object(video, "FFMPEG_PATH", ""), mock.patch.object(video, "_run") as run:
            video.convert_video(pid, src, self.Session)
        run.assert_not_called()
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(AsCasePhoto, pid))
        self.assertTrue(orig_path.exists())
        self.assertFalse(src.exists())

    def test_deleted_while_converting_discards_result(self):
        pid, orig_path, src = self.stored_and_committed()

        def run_and_customer_deletes(cmd, timeout):
            s = self.Session()
            s.delete(s.get(AsCasePhoto, pid))
            s.commit()
            s.close()
            return fake_run_ok(cmd, timeout)

        with mock.patch.object(video, "_run", side_effect=run_and_customer_deletes):
            video.convert_video(pid, src, self.Session)
        self.db.expire_all()
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)
        self.assertEqual(list((self.up / str(self.case.id)).glob("*.mp4")), [])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_convert_write_failure_keeps_original(self):
        pid, orig_path, src = self.stored_and_committed()
        with mock.patch.object(video, "_run", side_effect=fake_run_ok), \
                mock.patch.object(video.shutil, "copyfile", side_effect=OSError("nas down")), \
                self.assertLogs(video.log, "ERROR"):
            video.convert_video(pid, src, self.Session)
        self.db.expire_all()
        self.assertEqual(self.db.get(AsCasePhoto, pid).content_type, "video/quicktime")
        self.assertTrue(orig_path.exists())
        self.assertEqual(list(self.work.iterdir()), [])

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
        for target, name, value in ((photo, "AS_UPLOAD_DIR", self.tmp / "as"), (video, "AS_UPLOAD_DIR", self.tmp / "as"),
                                    (video, "VIDEO_WORK_DIR", self.tmp / "work")):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.jobs = []
        p = mock.patch.object(video, "schedule", side_effect=lambda pid, src: self.jobs.append((pid, src)))
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

    def up(self, data, name="v.mov", ctype="video/quicktime"):
        return self.client.post(f"/api/portal/cases/{self.case.id}/photos", headers=self.h,
                                files={"file": (name, data, ctype)})

    def test_upload_video_stores_original_and_schedules_conversion(self):
        r = self.up(MOV)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kind"], "video")
        self.assertEqual(len(self.jobs), 1)
        pid, src = self.jobs[0]
        self.assertEqual(pid, r.json()["id"])
        self.assertEqual(Path(src).read_bytes(), MOV)              # 변환은 작업 폴더의 복사본으로 한다
        d = self.client.get(f"/api/portal/cases/{self.case.id}", headers=self.h).json()
        self.assertEqual([p["kind"] for p in d["photos"]], ["video"])
        g = self.client.get(f"/api/portal/photos/{pid}", headers=self.h)
        self.assertEqual((g.status_code, g.headers["content-type"]), (200, "video/quicktime"))

    def test_upload_photo_still_works(self):
        r = self.up(JPEG, "a.jpg", "image/jpeg")
        self.assertEqual((r.status_code, r.json()["kind"]), (200, "photo"))
        self.assertEqual(self.jobs, [])

    def test_upload_rejects_other_files(self):
        r = self.up(b"MZ" + b"0" * 100, "x.exe", "application/octet-stream")
        self.assertEqual(r.status_code, 400)
        self.assertIn("영상", r.json()["detail"])
        self.assertEqual(self.jobs, [])

    def test_upload_oversize_video_is_400_and_leaves_no_work_file(self):
        with mock.patch.object(video, "VIDEO_MAX_BYTES", 100):
            r = self.up(MOV)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)
        work = self.tmp / "work"
        self.assertEqual(list(work.iterdir()) if work.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
