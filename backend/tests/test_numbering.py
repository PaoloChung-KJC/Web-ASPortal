"""접수번호 채번 검증: crm-rma 와 같은 규칙(AS + YYMM + 순번, 길이 10)."""
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AsCase, AutoNumberRule, Company
from app.services.numbering import next_as_no

D = date(2026, 11, 19)


class NumberingTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Company(id=1, name="가고객"))
        self.db.commit()

    def case(self, as_no):
        self.db.add(AsCase(as_no=as_no, company_id=1, received_date="2026-11-19", symptom="x"))
        self.db.commit()

    def test_first_number_of_month(self):
        self.assertEqual(next_as_no(self.db, D), "AS26110001")
        rule = self.db.query(AutoNumberRule).one()   # 규칙이 없으면 기본 규칙을 만든다
        self.assertEqual((rule.doc_code, rule.text_prefix, rule.date_format, rule.number_length),
                         ("AS", "AS", "YYMM", 10))

    def test_continues_after_existing(self):
        self.case("AS26110007")
        self.case("AS26100099")          # 지난달 번호는 세지 않는다
        self.assertEqual(next_as_no(self.db, D), "AS26110008")

    def test_uses_rule_changed_by_admin(self):
        self.db.add(AutoNumberRule(doc_code="AS", doc_name="A/S 번호", text_prefix="CS", date_format="YYYYMM",
                                   number_length=12))
        self.db.commit()
        self.assertEqual(next_as_no(self.db, D), "CS2026110001")


if __name__ == "__main__":
    unittest.main()
