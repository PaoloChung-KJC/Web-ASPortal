"""이 PC 에서 띄워 볼 때 쓰는 시험 자료 만들기 — SQLite(backend/dev.db) 전용.

운영 DB 에는 절대 쓰지 않는다: SQLite 가 아니면 아무것도 하지 않고 끝낸다.
표를 만드는 코드는 여기와 tests/ 에만 있다(프로그램 본체 app/ 에는 없다).
실행: backend 에서  .venv\\Scripts\\python.exe tools\\dev_seed.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import IS_SQLITE  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import AsPortalAccount, CommonCode, CommonCodeGroup, Company  # noqa: E402
from app.services.portal_auth import hash_password  # noqa: E402

# 로컬 전용 시험 계정 (이 PC 의 dev.db 에만 생긴다)
DEV_ACCOUNTS = [
    # (거래처 id, 로그인 ID, 처음 로그인 때 비밀번호를 바꿔야 하는가)
    (1, "demo.a", "N"),
    (2, "demo.b", "Y"),
]
DEV_PASSWORD = "local-dev-1234"
PRODUCT_GROUPS = [("K215", "21.5형 키오스크"), ("K238", "23.8형 키오스크"), ("ETC", "기타")]
KINDS = [("CS", "CS (현장)"), ("AS", "AS (입고 수리)")]   # crm-rma seed 의 AS_KIND 와 같은 값


def main() -> int:
    if not IS_SQLITE:
        print("운영 DB 에는 쓰지 않습니다 (SQLite 가 아닙니다). 아무것도 하지 않았습니다.")
        return 2
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if not db.get(Company, 1):
            db.add_all([Company(id=1, name="시험고객 가"), Company(id=2, name="시험고객 나")])
        for group_code, group_name, codes in (("AS_PRODUCT_GROUP", "A/S 제품군", PRODUCT_GROUPS),
                                              ("AS_KIND", "A/S 구분", KINDS)):
            grp = db.query(CommonCodeGroup).filter_by(group_code=group_code).first()
            if not grp:
                grp = CommonCodeGroup(group_code=group_code, group_name=group_name)
                db.add(grp)
                db.flush()
                for n, (code, name) in enumerate(codes):
                    db.add(CommonCode(group_id=grp.id, code=code, display_name=name, sort_order=n, use_yn="Y"))
        for company_id, login_id, must_change in DEV_ACCOUNTS:
            if not db.query(AsPortalAccount).filter_by(login_id=login_id).first():
                db.add(AsPortalAccount(company_id=company_id, login_id=login_id,
                                       hashed_password=hash_password(DEV_PASSWORD), must_change_pw=must_change))
        db.commit()
    finally:
        db.close()
    print("로컬 시험 자료 준비 완료. 계정:", ", ".join(a[1] for a in DEV_ACCOUNTS), "(비밀번호는 이 파일의 DEV_PASSWORD)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
