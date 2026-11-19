"""사진 검사·저장 검증: 메모리 SQLite + 임시 폴더, 실제 사진 폴더에는 쓰지 않는다."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AsCase, AsCasePhoto, Company
from app.services import photo
from app.services.photo import CUSTOMER_MAX, MAX_BYTES, photo_path, save_photo, sniff_image, trash_photo

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 100
STORAGE_DOWN = "사진을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요"


class PhotoTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(photo, "AS_UPLOAD_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db.add(Company(id=1, name="가고객"))
        self.case = AsCase(as_no="AS99120001", company_id=1, received_date="2099-12-31", symptom="x")
        self.db.add(self.case)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sniff_image(self):
        self.assertEqual(sniff_image(JPEG), ("image/jpeg", ".jpg"))
        self.assertEqual(sniff_image(PNG), ("image/png", ".png"))
        self.assertEqual(sniff_image(WEBP), ("image/webp", ".webp"))
        self.assertIsNone(sniff_image(b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 20))
        self.assertIsNone(sniff_image(b"MZ" + b"0" * 100))
        self.assertIsNone(sniff_image(b""))

    def test_rejects_non_image_and_oversize_with_message(self):
        for data, msg in [(b"MZ" + b"0" * 100, "사진 파일"), (b"<html>", "사진 파일"), (JPEG + b"0" * MAX_BYTES, "8MB")]:
            with self.assertRaises(HTTPException) as cm:
                save_photo(self.db, self.case, data, "x.jpg", "customer", "홍길동")
            self.assertEqual(cm.exception.status_code, 400)
            self.assertIn(msg, cm.exception.detail)
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)

    def test_saves_file_and_row(self):
        p = save_photo(self.db, self.case, PNG, "a" * 200 + ".png", "customer", "홍길동")
        self.assertIsNotNone(p.id)
        self.assertEqual((p.content_type, p.size, p.uploaded_by, p.uploaded_by_name),
                         ("image/png", len(PNG), "customer", "홍길동"))
        self.assertEqual(len(p.orig_name), 120)
        self.assertEqual(photo_path(p).read_bytes(), PNG)
        self.assertEqual(photo_path(p).parent, self.tmp / str(self.case.id))   # 직원 화면과 같은 폴더 규칙

    def test_stored_name_ignores_client_name(self):
        p = save_photo(self.db, self.case, JPEG, "../../etc/passwd.jpg", "customer", "홍길동")
        self.assertRegex(p.stored_name, r"^[0-9a-f]{32}\.jpg$")
        self.assertTrue(photo_path(p).resolve().is_relative_to(self.tmp.resolve()))

    def test_customer_limit_ignores_staff_photos(self):
        self.db.add(AsCasePhoto(case_id=self.case.id, stored_name="s.jpg", uploaded_by="staff"))
        self.db.commit()
        for _ in range(CUSTOMER_MAX):
            save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        with self.assertRaises(HTTPException) as cm:
            save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("최대 10장", cm.exception.detail)

    def test_write_failure_is_503_and_adds_no_row(self):
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("disk full")), self.assertLogs(photo.log, "ERROR"):
            with self.assertRaises(HTTPException) as cm:
                save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (503, STORAGE_DOWN))
        self.assertEqual(self.db.query(AsCasePhoto).count(), 0)

    def test_unreachable_folder_is_503(self):
        # NAS 공유 폴더가 끊긴 경우: 폴더를 만들 수 없다
        blocker = self.tmp / "not-a-folder"
        blocker.write_bytes(b"x")
        with mock.patch.object(photo, "AS_UPLOAD_DIR", blocker / "as"), self.assertLogs(photo.log, "ERROR"):
            with self.assertRaises(HTTPException) as cm:
                save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (503, STORAGE_DOWN))

    def test_trash_moves_file_and_is_idempotent(self):
        p = save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        src = photo_path(p)
        trash_photo(p)
        self.assertFalse(src.exists())
        self.assertTrue((self.tmp / "_trash" / p.stored_name).exists())
        trash_photo(p)

    def test_trash_overwrites_existing_trash_file(self):
        p = save_photo(self.db, self.case, JPEG, "a.jpg", "customer", "홍길동")
        (self.tmp / "_trash").mkdir()
        (self.tmp / "_trash" / p.stored_name).write_bytes(b"old")
        trash_photo(p)
        self.assertEqual((self.tmp / "_trash" / p.stored_name).read_bytes(), JPEG)


if __name__ == "__main__":
    unittest.main()
