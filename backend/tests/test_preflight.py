"""설치 점검(python -m app.preflight) 검증. 점검은 읽기만 한다 — 표를 만들지 않는다."""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app import preflight
from app.database import Base
from app.preflight import (
    REQUIRED_SEQUENCES, REQUIRED_TABLE_PRIVILEGES, check_upload_dir, missing_grants, missing_schema, run,
)


def memory_engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_quiet(self, engine, **patches):
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(preflight, name, value))
            stack.enter_context(contextlib.redirect_stdout(out))
            code = run(engine)
        return code, out.getvalue()

    def test_reports_missing_table_and_column(self):
        eng = memory_engine()
        Base.metadata.create_all(eng)
        with eng.begin() as c:
            c.execute(text("DROP TABLE as_case_log"))
            c.execute(text("ALTER TABLE as_case DROP COLUMN symptom"))
        self.assertEqual(sorted(missing_schema(eng, Base.metadata)), ["as_case.symptom 칸 없음", "표 as_case_log 없음"])

    def test_full_schema_reports_nothing(self):
        eng = memory_engine()
        Base.metadata.create_all(eng)
        self.assertEqual(missing_schema(eng, Base.metadata), [])

    def test_extra_columns_and_tables_in_db_are_fine(self):
        # crm-rma 가 칸·표를 더한 경우 — 고객 사이트는 자기가 쓰는 것만 본다
        eng = memory_engine()
        Base.metadata.create_all(eng)
        with eng.begin() as c:
            c.execute(text("ALTER TABLE company ADD COLUMN comp_code VARCHAR(40)"))
            c.execute(text("CREATE TABLE rma (id INTEGER PRIMARY KEY)"))
        self.assertEqual(missing_schema(eng, Base.metadata), [])

    def test_upload_dir_ok_missing_and_leaves_nothing_behind(self):
        self.assertIsNone(check_upload_dir(self.tmp))
        self.assertEqual(list(self.tmp.iterdir()), [])                 # 시험 파일을 남기지 않는다
        missing = self.tmp / "nope" / "as"
        self.assertIn("없습니다", check_upload_dir(missing))
        self.assertFalse(missing.exists())                             # 없는 폴더를 만들지 않는다

    def test_all_ok(self):
        eng = memory_engine()
        Base.metadata.create_all(eng)
        code, out = self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp, SECRET_KEY_IS_DEFAULT=False, IS_SQLITE=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("[확인]"), 4)
        self.assertNotIn("[문제]", out)

    def test_default_secret_on_server_db_is_a_problem(self):
        eng = memory_engine()
        Base.metadata.create_all(eng)
        code, out = self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp, SECRET_KEY_IS_DEFAULT=True, IS_SQLITE=False)
        self.assertEqual(code, 1)
        self.assertIn("[문제] SECRET_KEY", out)

    def test_counts_every_problem_and_names_them(self):
        eng = memory_engine()   # 표가 하나도 없다
        code, out = self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp / "nope", SECRET_KEY_IS_DEFAULT=True, IS_SQLITE=False)
        self.assertEqual(code, 1)
        self.assertEqual(out.count("[문제]"), 3)     # SECRET_KEY · 표 · 사진 폴더 (DB 접속은 된다)
        self.assertIn("표 as_case 없음", out)

    def test_db_down_is_reported_without_credentials(self):
        eng = create_engine("postgresql+psycopg2://someuser:TOPSECRETPW@127.0.0.1:1/crm_rma",
                            connect_args={"connect_timeout": 1})
        code, out = self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp, SECRET_KEY_IS_DEFAULT=False, IS_SQLITE=False)
        self.assertEqual(code, 1)
        self.assertIn("[문제] DB 접속", out)
        self.assertNotIn("TOPSECRETPW", out)

    # ---- 권한 (PostgreSQL 에서만 확인한다 — 표는 보이는데 권한이 없으면 첫 접수에서 500 이 난다) ----
    def test_missing_grants_names_table_privilege_and_sequence(self):
        def has(kind, name, priv):
            return not ((kind, name, priv) in {("table", "as_case", "INSERT"), ("sequence", "as_case_log_id_seq", "USAGE")})
        self.assertEqual(sorted(missing_grants(has)),
                         ["as_case 에 INSERT 권한 없음", "시퀀스 as_case_log_id_seq 사용 권한 없음"])

    def test_missing_grants_all_present(self):
        seen = []
        self.assertEqual(missing_grants(lambda kind, name, priv: seen.append((kind, name, priv)) or True), [])
        self.assertEqual(len(seen), sum(len(p) for p in REQUIRED_TABLE_PRIVILEGES.values()) + len(REQUIRED_SEQUENCES))

    def test_required_privileges_cover_every_portal_table(self):
        self.assertEqual(set(REQUIRED_TABLE_PRIVILEGES), set(Base.metadata.tables))
        self.assertEqual(REQUIRED_TABLE_PRIVILEGES["company"], ("SELECT",))
        self.assertNotIn("DELETE", REQUIRED_TABLE_PRIVILEGES["as_case"])

    def test_grants_are_not_checked_on_sqlite(self):
        eng = memory_engine()
        Base.metadata.create_all(eng)
        code, out = self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp, SECRET_KEY_IS_DEFAULT=False, IS_SQLITE=False)
        self.assertEqual(code, 0, out)
        self.assertNotIn("권한", out)

    def test_run_does_not_create_tables(self):
        eng = memory_engine()
        self.run_quiet(eng, AS_UPLOAD_DIR=self.tmp, SECRET_KEY_IS_DEFAULT=False, IS_SQLITE=False)
        self.assertEqual(inspect(eng).get_table_names(), [])


if __name__ == "__main__":
    unittest.main()
