"""환경 값 읽기 (고객 A/S 사이트).

이 PC(개발) : 값이 없으면 SQLite(backend/dev.db)
웹 서버(운영): backend/.env 의 DATABASE_URL 로 NAS PostgreSQL 에 접속
"""
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=True)

APP_TITLE = "KJC A/S 고객 사이트"

# DATABASE_URL 을 직접 주거나, DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME 으로 준다
_db_url = os.getenv("DATABASE_URL", "")
if not _db_url:
    _host = os.getenv("DB_HOST", "")
    _user = os.getenv("DB_USER", "")
    if _host and _user:
        _db_url = "postgresql+psycopg2://{}:{}@{}:{}/{}".format(
            _user, os.getenv("DB_PASSWORD", ""), _host, os.getenv("DB_PORT", "5432"), os.getenv("DB_NAME", "crm_rma"))
DATABASE_URL = _db_url or "sqlite:///" + (BASE_DIR / "dev.db").as_posix()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# 고객 토큰 서명 키. 비어 있으면 아래 개발용 값 — 운영 DB 에서는 이 값으로는 사이트를 열지 않는다(portal_auth.portal_enabled)
DEFAULT_SECRET_KEY = "asportal-dev-only-not-for-production"
SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip() or DEFAULT_SECRET_KEY
SECRET_KEY_IS_DEFAULT = SECRET_KEY == DEFAULT_SECRET_KEY

# 사진 폴더. 운영은 NAS 공유 폴더(UNC 경로) — crm-rma 가 /data/uploads/as 로 보는 바로 그 폴더
AS_UPLOAD_DIR = Path(os.getenv("AS_UPLOAD_DIR") or (BASE_DIR / "uploads" / "as"))

# 영상 — 고객이 올린 영상은 서버에서 ffmpeg 로 FHD MP4 로 바꿔 저장한다(원본은 지운다).
# ffmpeg 가 없거나 변환에 실패하면 원본을 그대로 둔다(접수 증거가 없어지지 않게)
FFMPEG_PATH = (os.getenv("FFMPEG_PATH") or "").strip() or shutil.which("ffmpeg") or ""
VIDEO_MAX_BYTES = int(os.getenv("VIDEO_MAX_MB") or 1024) * 1024 * 1024   # 원본 한 개 한도
VIDEO_TIMEOUT_SEC = int(os.getenv("VIDEO_TIMEOUT_SEC") or 3600)        # 한 개 변환 시간 한도
# 올라온 원본·변환 결과를 잠시 두는 서버 안 폴더(NAS 아님 — 변환이 빠르게)
VIDEO_WORK_DIR = Path(os.getenv("VIDEO_WORK_DIR") or (Path(tempfile.gettempdir()) / "asportal-video"))

# 요청자 IP 머리말(X-Forwarded-For)을 믿어도 되는 상대 = 같은 서버의 IIS
TRUSTED_PROXIES = frozenset(
    p.strip() for p in (os.getenv("TRUSTED_PROXIES") or "127.0.0.1,::1").split(",") if p.strip())

# 화면 파일 (Web-ASPortal/frontend)
FRONTEND_DIR = BASE_DIR.parent / "frontend"
