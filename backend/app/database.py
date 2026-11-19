"""DB 연결. 이 프로그램은 표를 만들거나 고치지 않는다 — 표 구조의 주인은 crm-rma 다."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL, IS_SQLITE

connect_args = {"check_same_thread": False} if IS_SQLITE else {}

# pool_pre_ping: NAS 와의 연결이 끊겼다 이어져도 다음 요청에서 스스로 다시 붙는다
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
