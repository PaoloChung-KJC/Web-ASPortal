"""A/S 접수번호 채번. crm-rma services/numbering.py 의 next_as_no 와 같은 규칙.

직원 화면과 고객 사이트가 같은 표(auto_number_rule · as_case)에서 번호를 뽑는다.
겹치면 as_case.as_no 의 유일 조건이 막고, 부르는 쪽(routers/portal.py)이 다시 뽑는다.
"""
from datetime import date

from sqlalchemy.orm import Session

from ..models import AsCase, AutoNumberRule


def _date_part(on: date, fmt: str) -> str:
    table = {
        "NONE": "",
        "YY": "%y",
        "YYYY": "%Y",
        "YYMM": "%y%m",
        "YYYYMM": "%Y%m",
        "YYMMDD": "%y%m%d",
        "YYYYMMDD": "%Y%m%d",
    }
    picked = table.get((fmt or "YYMMDD").upper(), "%y%m%d")
    return "" if picked == "" else on.strftime(picked)


def _rule(db: Session, code: str, fallback: dict) -> AutoNumberRule:
    rule = db.query(AutoNumberRule).filter_by(doc_code=code).first()
    if rule:
        return rule
    rule = AutoNumberRule(doc_code=code, **fallback)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _next_by_rule(db: Session, rule: AutoNumberRule, model, column, on: date) -> str:
    prefix = (rule.text_prefix or "") + _date_part(on, rule.date_format)
    seq_len = max(1, int(rule.number_length or 0) - len(prefix))
    values = db.query(column).filter(column.like(f"{prefix}%")).all()
    max_seq = 0
    for (value,) in values:
        suffix = str(value or "")[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    seq = max_seq + 1
    return f"{prefix}{seq:0{seq_len}d}"


def next_as_no(db: Session, on: date | None = None) -> str:
    on = on or date.today()
    rule = _rule(db, "AS", {
        "doc_name": "A/S 번호",
        "auto_yn": "Y",
        "text_prefix": "AS",
        "date_format": "YYMM",
        "number_length": 10,
    })
    return _next_by_rule(db, rule, AsCase, AsCase.as_no, on)
