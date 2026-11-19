"""표 정의 검증: 고객 사이트가 쓰는 표만, crm-rma 와 같은 칸, 표를 만드는 것은 테스트뿐."""
import unittest

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AsCase, AsCaseLog, AsCasePhoto, AsPortalAccount, Company

EXPECTED_TABLES = {"company", "common_code_group", "common_code", "auto_number_rule", "record_revision",
                   "as_portal_account", "as_case", "as_case_photo", "as_case_log"}


class ModelsTest(unittest.TestCase):
    def test_only_portal_tables_defined(self):
        self.assertEqual(set(Base.metadata.tables), EXPECTED_TABLES)

    def test_timestamp_columns_are_timezone_aware(self):
        for t in Base.metadata.tables.values():
            for c in t.columns:
                if isinstance(c.type, DateTime):
                    self.assertTrue(c.type.timezone, f"{t.name}.{c.name}")

    def test_as_case_has_no_dependency_on_item_or_rma_tables(self):
        table = Base.metadata.tables["as_case"]
        targets = {fk.column.table.name for fk in table.foreign_keys}
        self.assertEqual(targets, {"company", "as_portal_account"})
        self.assertIn("item_id", table.c)   # 칸은 있다(값은 그대로 둔다)
        self.assertIn("rma_id", table.c)

    def test_engine_checks_connection_before_use(self):
        from app.database import engine
        self.assertTrue(engine.pool._pre_ping)

    def test_roundtrip_in_sqlite(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        db.add(Company(id=1, name="가고객"))
        acc = AsPortalAccount(company_id=1, login_id="usera", hashed_password="x")
        db.add(acc)
        db.flush()
        case = AsCase(as_no="AS26110001", source="portal", company_id=1, portal_account_id=acc.id,
                      received_date="2026-11-19", symptom="화면 안 켜짐")
        case.photos.append(AsCasePhoto(stored_name="a.jpg", uploaded_by="customer"))
        case.logs.append(AsCaseLog(log_date="2026-11-19", content="접수"))
        db.add(case)
        db.commit()
        got = db.query(AsCase).one()
        self.assertEqual((got.company.name, got.status, got.qty, len(got.photos), len(got.logs)),
                         ("가고객", "received", 1, 1, 1))
        self.assertEqual(db.get(AsPortalAccount, acc.id).must_change_pw, "Y")


if __name__ == "__main__":
    unittest.main()
