"""고객 로그인 검증: 메모리 SQLite, 운영 DB 접근 없음. (crm-rma test_as_portal_auth.py 에서 고객 쪽만 옮김)"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AsPortalAccount
from app.services import portal_auth
from app.services.portal_auth import (
    LOGIN_WINDOW_MAX, LOGIN_WINDOW_SECONDS, authenticate, check_login_rate, create_portal_token,
    get_portal_account, hash_password, normalize_login_id, portal_enabled,
    reset_login_rate, set_password, valid_login_id,
)

NOT_READY = "서비스 준비 중입니다. 담당자에게 문의해 주세요"


def cred(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def raw_token(payload):
    """이 서버의 키로 서명한 임의 내용 토큰 (모양이 틀린 토큰을 만들어 보기 위한 것)."""
    data = {"exp": datetime.now(timezone.utc) + timedelta(hours=1), **payload}
    return jwt.encode(data, portal_auth.SECRET_KEY, algorithm="HS256")


class PortalAuthTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        reset_login_rate()
        self.addCleanup(reset_login_rate)

    def tearDown(self):
        self.db.close()

    def make_account(self, login_id, pw, **kw):
        acc = AsPortalAccount(company_id=1, login_id=login_id, hashed_password=hash_password(pw), **kw)
        self.db.add(acc)
        self.db.commit()
        return acc

    def status_of(self, fn, *a, **kw):
        with self.assertRaises(HTTPException) as cm:
            fn(*a, **kw)
        return cm.exception.status_code, cm.exception.detail

    def test_login_id_is_trimmed_and_lowercased(self):
        self.make_account("maejang", "Passw0rd!x")
        self.assertEqual(authenticate(self.db, "  MaeJang ", "Passw0rd!x").login_id, "maejang")

    def test_login_id_helpers(self):
        self.assertEqual(normalize_login_id("  AbC "), "abc")
        self.assertTrue(valid_login_id("ab.c_-9"))
        self.assertFalse(valid_login_id("abc"))
        self.assertFalse(valid_login_id("A" * 5))
        self.assertFalse(valid_login_id("a" * 31))

    def test_wrong_password_same_message_as_unknown_id(self):
        self.make_account("maejang", "Passw0rd!x")
        a = self.status_of(authenticate, self.db, "maejang", "wrong")
        b = self.status_of(authenticate, self.db, "nobody", "wrong")
        self.assertEqual(a, b)
        self.assertEqual(a, (401, "아이디 또는 비밀번호가 올바르지 않습니다"))

    def test_lock_after_five_failures_then_unlock(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        for _ in range(5):
            self.assertEqual(self.status_of(authenticate, self.db, "maejang", "bad")[0], 401)
        self.assertEqual(self.status_of(authenticate, self.db, "maejang", "Passw0rd!x"),
                         (429, "잠시 후 다시 시도해 주세요"))
        acc = self.db.query(AsPortalAccount).filter_by(login_id="maejang").one()
        acc.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.commit()
        got = authenticate(self.db, "maejang", "Passw0rd!x", ip="1.2.3.4")
        self.assertEqual(got.fail_count, 0)
        self.assertIsNotNone(got.last_login_at)
        self.assertEqual(got.last_login_ip, "1.2.3.4")

    def test_parallel_failures_are_not_lost(self):
        self.make_account("maejang", "Passw0rd!x")
        other = self.Session()
        try:
            stale = self.db.query(AsPortalAccount).filter_by(login_id="maejang").one()
            self.assertEqual(stale.fail_count, 0)  # 이 세션은 0 으로 알고 있다
            for _ in range(4):  # 다른 세션이 먼저 4번 실패를 커밋
                self.assertEqual(self.status_of(authenticate, other, "maejang", "bad")[0], 401)
            self.assertEqual(self.status_of(authenticate, self.db, "maejang", "bad")[0], 401)
            fresh = other.query(AsPortalAccount).filter_by(login_id="maejang").one()
            other.refresh(fresh)
            self.assertEqual(fresh.fail_count, 5)
            self.assertIsNotNone(fresh.locked_until)
        finally:
            other.close()

    def test_disabled_account_cannot_login(self):
        self.make_account("maejang", "Passw0rd!x", use_yn="N")
        self.assertEqual(self.status_of(authenticate, self.db, "maejang", "Passw0rd!x")[0], 401)

    def test_token_rejected_after_password_change_or_disable(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        token = create_portal_token(acc)
        self.assertEqual(get_portal_account(cred(token), self.db).id, acc.id)
        set_password(self.db, acc, "NewPassw0rd", must_change=False)
        self.assertEqual(self.status_of(get_portal_account, cred(token), self.db),
                         (401, "로그인이 필요합니다"))
        fresh = create_portal_token(acc)
        self.assertEqual(get_portal_account(cred(fresh), self.db).id, acc.id)
        acc.use_yn = "N"
        self.db.commit()
        self.assertEqual(self.status_of(get_portal_account, cred(fresh), self.db)[0], 401)

    def test_set_password_rules(self):
        acc = self.make_account("maejang", "Passw0rd!x", fail_count=5,
                                locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
        self.assertEqual(self.status_of(set_password, self.db, acc, "short", must_change=False)[0], 400)
        set_password(self.db, acc, "LongEnough1", must_change=True)
        self.assertEqual(acc.token_ver, 1)
        self.assertEqual(acc.must_change_pw, "Y")
        self.assertEqual(acc.fail_count, 0)
        self.assertIsNone(acc.locked_until)
        self.assertIsNotNone(acc.pw_changed_at)

    def test_malformed_tokens_are_401(self):
        self.assertEqual(self.status_of(get_portal_account, None, self.db)[0], 401)
        self.assertEqual(self.status_of(get_portal_account, cred("garbage"), self.db)[0], 401)
        bad = raw_token({"typ": "portal", "sub": "portal:abc", "tv": 0})
        self.assertEqual(self.status_of(get_portal_account, cred(bad), self.db)[0], 401)

    # ---- 운영 SECRET_KEY 미설정 시 포털 차단 ----
    def test_portal_disabled_with_default_secret_on_server_db(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        token = create_portal_token(acc)
        with patch.object(portal_auth, "SECRET_KEY_IS_DEFAULT", True), patch.object(portal_auth, "IS_SQLITE", False):
            self.assertFalse(portal_enabled())
            self.assertEqual(self.status_of(authenticate, self.db, "maejang", "Passw0rd!x"), (503, NOT_READY))
            self.assertEqual(self.status_of(get_portal_account, cred(token), self.db), (503, NOT_READY))
            self.assertEqual(self.status_of(get_portal_account, None, self.db), (503, NOT_READY))
            with self.assertRaises(RuntimeError):
                create_portal_token(acc)
        self.db.refresh(acc)
        self.assertEqual(acc.fail_count, 0)  # 막힌 요청은 실패 횟수에 들어가지 않는다

    def test_portal_enabled_with_own_secret_on_server_db(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        with patch.object(portal_auth, "SECRET_KEY_IS_DEFAULT", False), patch.object(portal_auth, "IS_SQLITE", False):
            self.assertTrue(portal_enabled())
            self.assertEqual(authenticate(self.db, "maejang", "Passw0rd!x").id, acc.id)
            token = create_portal_token(acc)
            self.assertEqual(get_portal_account(cred(token), self.db).id, acc.id)

    def test_portal_enabled_on_sqlite_even_with_default_secret(self):
        with patch.object(portal_auth, "SECRET_KEY_IS_DEFAULT", True), patch.object(portal_auth, "IS_SQLITE", True):
            self.assertTrue(portal_enabled())

    # ---- 로그인 시도 횟수 제한 (프로세스 전체) ----
    def test_login_rate_limit_sliding_window(self):
        t = 1000.0
        for i in range(LOGIN_WINDOW_MAX):
            check_login_rate(now=t + i * 0.1)
        self.assertEqual(self.status_of(check_login_rate, now=t + 5), (429, "잠시 후 다시 시도해 주세요"))
        check_login_rate(now=t + LOGIN_WINDOW_SECONDS + 5)  # 창이 지나면 다시 된다
        reset_login_rate()
        for _ in range(LOGIN_WINDOW_MAX):
            check_login_rate(now=t)
        self.assertEqual(self.status_of(check_login_rate, now=t)[0], 429)

    # ---- 잠금이 풀린 뒤 실패 횟수 ----
    def test_expired_lock_resets_fail_count(self):
        self.make_account("maejang", "Passw0rd!x")
        for _ in range(5):
            self.status_of(authenticate, self.db, "maejang", "bad")
        acc = self.db.query(AsPortalAccount).filter_by(login_id="maejang").one()
        self.assertIsNotNone(acc.locked_until)
        acc.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.commit()
        self.assertEqual(self.status_of(authenticate, self.db, "maejang", "bad")[0], 401)
        self.db.refresh(acc)
        self.assertEqual(acc.fail_count, 1)
        self.assertIsNone(acc.locked_until)

    # ---- 비밀번호 최대 길이 (bcrypt 72바이트) ----
    def test_set_password_max_72_bytes(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        for too_long in ("a" * 73, "가" * 25):  # 한글 25자 = 75바이트
            self.assertEqual(self.status_of(set_password, self.db, acc, too_long, must_change=False),
                             (400, "비밀번호는 72바이트 이하여야 합니다"))
        set_password(self.db, acc, "a" * 72, must_change=False)
        self.assertEqual(authenticate(self.db, "maejang", "a" * 72).id, acc.id)

    # ---- 직원 모양 토큰 · 다른 서버의 키 ----
    def test_staff_style_token_is_rejected(self):
        self.make_account("maejang", "Passw0rd!x")
        self.assertEqual(self.status_of(get_portal_account, cred(raw_token({"sub": "admin"})), self.db)[0], 401)
        self.assertEqual(self.status_of(get_portal_account, cred(raw_token({"typ": "staff", "sub": "portal:1", "tv": 0})),
                                        self.db)[0], 401)

    def test_token_signed_with_another_key_is_rejected(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        foreign = jwt.encode({"typ": "portal", "sub": f"portal:{acc.id}", "tv": 0,
                              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "crm-side-key", algorithm="HS256")
        self.assertEqual(self.status_of(get_portal_account, cred(foreign), self.db)[0], 401)

    def test_token_lives_seven_days(self):
        acc = self.make_account("maejang", "Passw0rd!x")
        claims = jwt.get_unverified_claims(create_portal_token(acc))
        left = datetime.fromtimestamp(claims["exp"], timezone.utc) - datetime.now(timezone.utc)
        self.assertTrue(timedelta(days=6, hours=23) < left <= timedelta(days=7))
        self.assertEqual((claims["typ"], claims["sub"], claims["tv"]), ("portal", f"portal:{acc.id}", 0))

    def test_account_issuing_is_not_here(self):
        # 계정 발급(초기 비밀번호 만들기)은 crm-rma 직원 화면의 일이다
        self.assertFalse(hasattr(portal_auth, "generate_initial_password"))


if __name__ == "__main__":
    unittest.main()
