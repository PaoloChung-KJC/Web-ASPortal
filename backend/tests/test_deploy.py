"""배포 파일 검증: IIS 설정이 약속(프로세스 1개 · 127.0.0.1 · 10MB)을 지키고, 배포 파일에 비밀 값이 없다."""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # Web-ASPortal/
DEPLOY = ROOT / "deploy"


class DeployFilesTest(unittest.TestCase):
    def test_web_config_is_wellformed_and_single_process(self):
        root = ET.parse(DEPLOY / "web.config").getroot()
        hp = root.find(".//httpPlatform")
        self.assertIsNotNone(hp)
        self.assertEqual(hp.get("processesPerApplication"), "1")
        args = hp.get("arguments")
        self.assertIn("uvicorn", args)
        self.assertIn("app.main:app", args)
        self.assertIn("%HTTP_PLATFORM_PORT%", args)
        self.assertIn("--host 127.0.0.1", args)
        self.assertNotIn("--workers", args)
        # uvicorn 이 X-Forwarded-For 를 먼저 풀어 버리면 client_ip.py 의 규칙(마지막 값·포트 떼기)을 거치지 않는다
        self.assertIn("--no-proxy-headers", args)
        self.assertNotIn("--reload", args)
        self.assertEqual(root.find(".//requestLimits").get("maxAllowedContentLength"), "10485760")

    def test_web_config_hides_program_folders(self):
        root = ET.parse(DEPLOY / "web.config").getroot()
        hidden = {e.get("segment") for e in root.findall(".//hiddenSegments/add")}
        self.assertTrue({"backend", "deploy", ".git"} <= hidden, hidden)

    def test_web_config_has_no_settings_values(self):
        text = (DEPLOY / "web.config").read_text(encoding="utf-8")
        for word in ("DATABASE_URL", "SECRET_KEY", "postgresql", "password"):
            self.assertNotIn(word.lower(), text.lower(), word)

    def test_no_secrets_or_real_addresses_in_deploy_files(self):
        files = [p for p in DEPLOY.iterdir() if p.is_file()] + [ROOT / "backend" / ".env.example", ROOT / "README.md"]
        ip = re.compile(r"\b(?!127\.0\.0\.1\b)(?:\d{1,3}\.){3}\d{1,3}\b")
        for f in files:
            text = f.read_text(encoding="utf-8")
            self.assertEqual(ip.findall(text), [], f"{f.name}: 실제 IP 모양")
            for line in text.splitlines():
                m = re.match(r"\s*(DATABASE_URL|SECRET_KEY|DB_PASSWORD)\s*=\s*(\S+)", line)
                if m:
                    self.assertTrue(m.group(2).startswith("<"), f"{f.name}: {m.group(1)} 에 값이 있다")
            self.assertNotRegex(text, r"postgresql\+psycopg2://(?!<)[^\s<]+:[^\s<]+@", f.name)

    def test_db_account_sql_grants_only_portal_tables(self):
        sql = (DEPLOY / "db-account.sql").read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        granted = set(re.findall(r"\bON\s+(?:TABLE\s+)?([a-z_]+)\s+TO\b", body, re.I))
        self.assertEqual(granted, {"company", "common_code", "common_code_group", "as_portal_account", "as_case",
                                   "as_case_photo", "as_case_log", "record_revision", "auto_number_rule"})
        self.assertNotRegex(body, r"(?i)GRANT\s+ALL")
        self.assertNotRegex(body, r"(?i)\b(SUPERUSER|CREATEDB|CREATEROLE)\b(?<!NO)")
        self.assertIn("<PORTAL_DB_USER>", body)
        self.assertIn("<PORTAL_DB_PASSWORD>", body)
        # 거래처 표는 id · name 두 칸만 읽는다 (사업자번호·연락처 등은 못 읽는다)
        self.assertRegex(body, r"(?i)GRANT\s+SELECT\s*\(\s*id\s*,\s*name\s*\)\s+ON\s+company\s+TO")
        self.assertNotRegex(body, r"(?i)GRANT\s+SELECT\s+ON\s+company")
        # 고객 사이트가 지우는 것은 사진 줄뿐이다
        self.assertEqual(re.findall(r"(?i)GRANT[^;]*\bDELETE\b[^;]*ON\s+([a-z_]+)", body), ["as_case_photo"])

    def test_sql_grants_equal_what_preflight_checks(self):
        # db-account.sql 의 권한과 preflight 가 확인하는 권한이 같아야 한다 (한쪽만 고치면 여기서 잡힌다)
        from app.preflight import REQUIRED_SEQUENCES, REQUIRED_TABLE_PRIVILEGES
        sql = (DEPLOY / "db-account.sql").read_text(encoding="utf-8")
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        tables = {}
        for privs, table in re.findall(r"(?i)GRANT\s+([A-Z, ()a-z_]+?)\s+ON\s+([a-z_]+)\s+TO", body):
            names = {re.sub(r"\s*\(.*\)", "", p).strip().upper() for p in re.split(r",(?![^(]*\))", privs)}
            tables[table] = names
        self.assertEqual(tables, {t: set(p) for t, p in REQUIRED_TABLE_PRIVILEGES.items()})
        seqs = set(re.findall(r"(?i)ON\s+SEQUENCE\s+([a-z_]+)\s+TO", body))
        self.assertEqual(seqs, set(REQUIRED_SEQUENCES))

    def test_install_guide_puts_web_config_in_site_root_and_checks_health_first(self):
        text = (DEPLOY / "install.md").read_text(encoding="utf-8")
        self.assertIn(r"C:\Webproject\Web-ASPortal\web.config", text)
        self.assertLess(text.index("/api/portal/health"), text.index("Uvicorn running"))   # 먼저 한 번 불러야 프로그램이 뜬다

    def test_install_guide_states_the_must_haves(self):
        text = (DEPLOY / "install.md").read_text(encoding="utf-8")
        for must in ("실제 서버에서 한 번도 실행해 보지 못했다", "관리자가 아닌", "SQL Server", "443", "1433",
                     "python -m app.preflight", "되돌리기", "HttpPlatformHandler", "db-account.sql"):
            self.assertIn(must, text, must)


if __name__ == "__main__":
    unittest.main()
