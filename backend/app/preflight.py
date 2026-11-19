"""설치 점검 — 서버에 올린 뒤, 또 새 버전을 올릴 때마다 돌린다.

    backend 폴더에서:  .venv\\Scripts\\python.exe -m app.preflight

네 가지(PostgreSQL 이면 권한까지 다섯 가지)를 보고 한 줄씩 [확인] / [문제] 로 찍는다. 문제가 하나라도 있으면 종료 코드 1.
읽기만 한다 — 표를 만들거나 고치지 않고, 사진 폴더에는 시험 파일을 썼다가 바로 지운다.
접속 주소·비밀번호는 찍지 않는다.
"""
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from .config import AS_UPLOAD_DIR, IS_SQLITE, SECRET_KEY_IS_DEFAULT
from .database import Base, engine as default_engine
from . import models  # noqa: F401  — 표 정의를 Base.metadata 에 올린다


# 고객 사이트 전용 DB 계정에 필요한 권한 — deploy/db-account.sql 과 같아야 한다 (tests/test_deploy.py 가 대조한다)
REQUIRED_TABLE_PRIVILEGES = {
    "company": ("SELECT",),                               # id · name 두 칸만
    "common_code": ("SELECT",),
    "common_code_group": ("SELECT",),
    "as_portal_account": ("SELECT", "UPDATE"),
    "as_case": ("SELECT", "INSERT", "UPDATE"),
    "as_case_photo": ("SELECT", "INSERT", "DELETE"),
    "as_case_log": ("SELECT", "INSERT"),
    "record_revision": ("SELECT", "INSERT", "UPDATE"),
    "auto_number_rule": ("SELECT", "INSERT"),
}
REQUIRED_SEQUENCES = ("as_case_id_seq", "as_case_photo_id_seq", "as_case_log_id_seq", "auto_number_rule_id_seq")
_COLUMN_ONLY = {"company": ("id", "name")}                # 표 전체가 아니라 이 칸들에만 권한을 준다


def missing_grants(has) -> list[str]:
    """has(kind, name, privilege) -> bool 로 물어 빠진 권한을 모은다. kind 는 "table" 또는 "sequence"."""
    problems = []
    for table, privileges in REQUIRED_TABLE_PRIVILEGES.items():
        for privilege in privileges:
            if not has("table", table, privilege):
                problems.append(f"{table} 에 {privilege} 권한 없음")
    for sequence in REQUIRED_SEQUENCES:
        if not has("sequence", sequence, "USAGE"):
            problems.append(f"시퀀스 {sequence} 사용 권한 없음")
    return problems


def _pg_has(conn):
    """PostgreSQL 에 지금 접속한 계정의 권한을 묻는 함수를 만든다."""
    def has(kind, name, privilege):
        try:
            if kind == "sequence":
                sql = text("SELECT has_sequence_privilege(current_user, :n, :p)")
                return bool(conn.execute(sql, {"n": name, "p": privilege}).scalar())
            columns = _COLUMN_ONLY.get(name)
            if columns:
                sql = text("SELECT has_column_privilege(current_user, :n, :c, :p)")
                return all(conn.execute(sql, {"n": name, "c": c, "p": privilege}).scalar() for c in columns)
            sql = text("SELECT has_table_privilege(current_user, :n, :p)")
            return bool(conn.execute(sql, {"n": name, "p": privilege}).scalar())
        except Exception:
            conn.rollback()      # 없는 표·시퀀스 이름이면 오류 → 권한 없음으로 본다
            return False
    return has


def missing_schema(engine, metadata) -> list[str]:
    """표 정의에 있는데 실제 DB 에 없는 표·칸. DB 에 더 있는 것은 상관없다(crm-rma 가 더한 것)."""
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    problems = []
    for table in metadata.sorted_tables:
        if table.name not in existing:
            problems.append(f"표 {table.name} 없음")
            continue
        columns = {c["name"] for c in insp.get_columns(table.name)}
        for column in table.columns:
            if column.name not in columns:
                problems.append(f"{table.name}.{column.name} 칸 없음")
    return problems


def check_upload_dir(path: Path) -> str | None:
    """사진 폴더에 쓸 수 있으면 None, 아니면 사유. 폴더가 없으면 만들지 않고 문제로 알린다."""
    path = Path(path)
    if not path.is_dir():
        return f"사진 폴더가 없습니다: {path}"
    probe = path / f".preflight-{uuid4().hex}.tmp"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        return f"사진 폴더에 쓸 수 없습니다: {path} ({e.__class__.__name__})"
    return None


def run(engine=None) -> int:
    """점검 4가지를 찍고, 문제가 있으면 1 · 없으면 0 을 돌려준다."""
    engine = engine or default_engine
    problems = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        if not ok:
            problems += 1
        print(f"[{'확인' if ok else '문제'}] {label}" + (f" — {detail}" if detail else ""))

    # ① 고객 토큰 서명 키
    if SECRET_KEY_IS_DEFAULT and not IS_SQLITE:
        report(False, "SECRET_KEY", "backend\\.env 에 SECRET_KEY 가 없습니다. 넣기 전에는 고객 사이트가 열리지 않습니다")
    else:
        report(True, "SECRET_KEY")

    # ② DB 접속  ③ 표·칸
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:   # 접속 문자열·비밀번호가 섞인 원문은 찍지 않는다
        report(False, "DB 접속", f"접속하지 못했습니다 ({e.__class__.__name__}). 주소·포트·계정·NAS 방화벽을 확인하세요")
        report(False, "표·칸", "DB 에 접속하지 못해 확인하지 못했습니다")
    else:
        report(True, "DB 접속")
        missing = missing_schema(engine, Base.metadata)
        if missing:
            report(False, "표·칸", "; ".join(missing) + "  (crm-rma 를 먼저 최신으로 올렸는지 확인하세요)")
        else:
            report(True, "표·칸")
        # 표는 보여도 권한이 없으면 첫 로그인·접수에서 오류가 난다 → PostgreSQL 에서는 권한도 본다
        if engine.dialect.name == "postgresql":
            with engine.connect() as conn:
                lacking = missing_grants(_pg_has(conn))
            if lacking:
                report(False, "DB 계정 권한", "; ".join(lacking) + "  (deploy\\db-account.sql 을 다시 확인하세요)")
            else:
                report(True, "DB 계정 권한")

    # ④ 사진 폴더
    reason = check_upload_dir(AS_UPLOAD_DIR)
    report(reason is None, "사진 폴더", reason or "")

    return 1 if problems else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(run())
