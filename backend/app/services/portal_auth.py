"""고객 로그인 — 토큰·계정이 직원(CRM) 쪽과 완전히 분리된다.

crm-rma services/portal_auth.py 에서 고객 로그인 부분을 옮긴 것. 계정 발급(초기 비밀번호 만들기)은
crm-rma 직원 화면의 일이라 여기에는 없다. 고객 토큰은 typ="portal" 이고 이 서버만의 SECRET_KEY 로 서명한다.
로그인 시도 제한이 프로세스 안 기억이므로 이 프로그램은 프로세스 1개로 돌린다.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import IS_SQLITE, SECRET_KEY, SECRET_KEY_IS_DEFAULT
from ..database import get_db
from ..models import AsPortalAccount

ALGORITHM = "HS256"
MAX_FAILS = 5
LOCK_MINUTES = 10
TOKEN_DAYS = 7
MIN_PW_LEN = 8
MAX_PW_BYTES = 72  # bcrypt 는 72바이트 뒤를 말없이 버린다

# 로그인 시도 제한 (프로세스 전체). 없는 ID 도 bcrypt 비용이 들어 한 스크립트로 서버 전체가 멈출 수 있다
LOGIN_WINDOW_SECONDS = 10
LOGIN_WINDOW_MAX = 20

LOGIN_FAIL_MSG = "아이디 또는 비밀번호가 올바르지 않습니다"
LOCKED_MSG = "잠시 후 다시 시도해 주세요"
NEED_LOGIN_MSG = "로그인이 필요합니다"
NOT_READY_MSG = "서비스 준비 중입니다. 담당자에게 문의해 주세요"

_login_times: deque = deque()
_login_lock = threading.Lock()  # 동기 엔드포인트는 스레드풀에서 동시에 돈다

_LOGIN_ID_RE = re.compile(r"^[a-z0-9._-]{4,30}$")

portal_bearer = HTTPBearer(auto_error=False)


def portal_enabled() -> bool:
    """운영 DB 인데 SECRET_KEY 가 기본값이면 토큰 위조가 가능하므로 고객 사이트를 막는다."""
    return not (SECRET_KEY_IS_DEFAULT and not IS_SQLITE)


def _require_enabled() -> None:
    if not portal_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_READY_MSG)


def check_login_rate(now: Optional[float] = None) -> None:
    """최근 LOGIN_WINDOW_SECONDS 초 안의 로그인 시도가 LOGIN_WINDOW_MAX 번을 넘으면 429. 막힌 시도는 세지 않는다."""
    t = time.monotonic() if now is None else now
    with _login_lock:
        while _login_times and _login_times[0] <= t - LOGIN_WINDOW_SECONDS:
            _login_times.popleft()
        if len(_login_times) >= LOGIN_WINDOW_MAX:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED_MSG)
        _login_times.append(t)


def reset_login_rate() -> None:
    """테스트용 — 기록된 로그인 시도를 비운다."""
    with _login_lock:
        _login_times.clear()


def normalize_login_id(raw: str) -> str:
    return (raw or "").strip().lower()


def valid_login_id(s: str) -> bool:
    return bool(_LOGIN_ID_RE.match(s or ""))


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# 없는 ID 도 같은 시간 비용으로 비교하기 위한 가짜 해시
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    # SQLite 는 시각대 없는 값을 돌려줄 수 있다 → UTC 로 간주
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def create_portal_token(account: AsPortalAccount) -> str:
    if not portal_enabled():
        raise RuntimeError("SECRET_KEY 가 기본값이라 고객 토큰을 만들 수 없습니다")
    payload = {
        "typ": "portal",
        "sub": f"portal:{account.id}",
        "tv": account.token_ver or 0,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def authenticate(db: Session, login_id: str, password: str, ip: str = "") -> AsPortalAccount:
    """로그인 검증. 실패·성공 기록은 여기서 commit 한다(예외를 던져도 남도록)."""
    _require_enabled()
    acc = db.query(AsPortalAccount).filter_by(login_id=normalize_login_id(login_id)).first()
    now = datetime.now(timezone.utc)
    if acc is not None:
        locked = _aware(acc.locked_until)
        if locked and locked > now:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED_MSG)
        if locked:
            # 잠금이 풀렸으면 실패 횟수도 처음부터 (안 그러면 한 번 틀려도 바로 다시 잠긴다)
            acc.fail_count = 0
            acc.locked_until = None
            db.flush()
    ok = verify_password(password or "", acc.hashed_password if acc is not None else _DUMMY_HASH)
    if acc is None or acc.use_yn != "Y" or not ok:
        if acc is not None and acc.use_yn == "Y":
            # 동시 요청에서 카운터가 유실되지 않도록 SQL 에서 원자적으로 올린다
            db.query(AsPortalAccount).filter_by(id=acc.id).update(
                {AsPortalAccount.fail_count: func.coalesce(AsPortalAccount.fail_count, 0) + 1},
                synchronize_session=False,
            )
            db.flush()
            db.refresh(acc)
            if acc.fail_count >= MAX_FAILS:
                acc.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_FAIL_MSG)
    acc.fail_count = 0
    acc.locked_until = None
    acc.last_login_at = now
    acc.last_login_ip = (ip or "")[:64]
    db.commit()
    return acc


def get_portal_account(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(portal_bearer),
    db: Session = Depends(get_db),
) -> AsPortalAccount:
    def deny():
        return HTTPException(status.HTTP_401_UNAUTHORIZED, NEED_LOGIN_MSG, headers={"WWW-Authenticate": "Bearer"})

    _require_enabled()
    if not credentials:
        raise deny()
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("typ") != "portal":
            raise deny()
        sub = str(payload.get("sub") or "")
        if not sub.startswith("portal:"):
            raise deny()
        acc_id = int(sub.split(":", 1)[1])
        tv = int(payload.get("tv"))
    except HTTPException:
        raise
    except (JWTError, ValueError, TypeError):
        raise deny()
    acc = db.get(AsPortalAccount, acc_id)
    if acc is None or acc.use_yn != "Y" or (acc.token_ver or 0) != tv:
        raise deny()
    return acc


def set_password(db: Session, account: AsPortalAccount, new_pw: str, *, must_change: bool) -> None:
    if len(new_pw or "") < MIN_PW_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"비밀번호는 {MIN_PW_LEN}자 이상이어야 합니다")
    if len(new_pw.encode("utf-8")) > MAX_PW_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"비밀번호는 {MAX_PW_BYTES}바이트 이하여야 합니다")
    account.hashed_password = hash_password(new_pw)
    account.must_change_pw = "Y" if must_change else "N"
    account.token_ver = (account.token_ver or 0) + 1
    account.pw_changed_at = datetime.now(timezone.utc)
    account.fail_count = 0
    account.locked_until = None
    db.commit()
