"""앱 전체 검증: 고객 주소로 닿는 것은 /api/portal/ 과 화면 파일뿐이다."""
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from app import main as main_mod
from app.main import create_app

MARK = "<h1>portal</h1>"


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "index.html").write_text(f"<html>{MARK}</html>", encoding="utf-8")
        (self.tmp / "portal.css").write_text("body{}", encoding="utf-8")
        self.client = TestClient(create_app(frontend_dir=self.tmp))

    def test_docs_are_closed(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_other_api_paths_are_404_json_not_html(self):
        for path in ("/api/as/cases", "/api/health", "/api/portal/nope", "/api/", "/api"):
            for method in ("get", "post", "put", "delete"):
                r = getattr(self.client, method)(path)
                self.assertEqual(r.status_code, 404, f"{method} {path}")
                self.assertNotIn(MARK, r.text)
                self.assertEqual(r.headers["content-type"], "application/json", path)

    def test_root_serves_frontend(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(MARK, r.text)
        self.assertEqual(self.client.get("/portal.css").text, "body{}")

    def test_unknown_page_is_404(self):
        self.assertEqual(self.client.get("/nope.html").status_code, 404)
        self.assertEqual(self.client.get("/../backend/.env").status_code, 404)

    def test_security_headers_on_every_response(self):
        for path in ("/", "/api/portal/me", "/api/portal/health", "/nope", "/docs"):
            h = self.client.get(path).headers
            self.assertEqual((h.get("x-content-type-options"), h.get("x-frame-options")), ("nosniff", "DENY"), path)

    def test_health(self):
        r = self.client.get("/api/portal/health")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_portal_api_needs_login(self):
        r = self.client.get("/api/portal/cases")
        self.assertEqual((r.status_code, r.json()["detail"]), (401, "로그인이 필요합니다"))

    def test_frontend_dir_missing_still_starts(self):
        client = TestClient(create_app(frontend_dir=self.tmp / "nope"))
        self.assertEqual(client.get("/api/portal/health").status_code, 200)
        self.assertEqual(client.get("/").status_code, 404)

    def test_startup_creates_no_tables(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with unittest.mock.patch.object(main_mod, "engine", engine, create=True):
            with TestClient(create_app(frontend_dir=self.tmp)) as client:   # with: 시작 처리까지 돌린다
                self.assertEqual(client.get("/api/portal/health").status_code, 200)
        self.assertEqual(inspect(engine).get_table_names(), [])

    def test_source_never_creates_or_alters_tables(self):
        # 표 구조의 주인은 crm-rma. 프로그램 코드(app/)에는 표를 만들거나 고치는 호출이 없어야 한다
        app_dir = Path(main_mod.__file__).resolve().parent
        for f in app_dir.rglob("*.py"):
            text = f.read_text(encoding="utf-8")
            for banned in ("create_all(", "drop_all(", "ALTER TABLE", "CREATE TABLE"):
                self.assertNotIn(banned, text, f"{f.name}: {banned}")


if __name__ == "__main__":
    import unittest.mock
    unittest.main()
