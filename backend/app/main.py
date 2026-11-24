"""고객 A/S 사이트 앱.

고객 주소로 닿는 것은 /api/portal/ 과 화면 파일(frontend/)뿐이다. 문서 주소(/docs 등)는 끈다.
이 프로그램은 DB 표를 만들거나 고치지 않는다(표 구조의 주인은 crm-rma) — 시작할 때도 DB 를 건드리지 않는다.
실행: uvicorn app.main:app --host 127.0.0.1 --port <포트>   (프로세스 1개 — 로그인 시도 제한이 프로세스 안 기억)
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_TITLE, AS_UPLOAD_DIR, FFMPEG_PATH, FRONTEND_DIR
from .routers import portal
from .services.portal_auth import portal_enabled

log = logging.getLogger("asportal")

SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not portal_enabled():
        log.warning("SECRET_KEY 가 설정되지 않아 고객 사이트를 막았습니다. backend/.env 에 SECRET_KEY 를 넣으면 열립니다.")
    upload_dir = Path(AS_UPLOAD_DIR)
    if upload_dir.exists() and not os.access(upload_dir, os.W_OK):
        log.warning("사진 폴더에 쓸 수 없습니다: %s", upload_dir)
    elif not upload_dir.exists():
        log.info("사진 폴더가 아직 없습니다(첫 사진을 올릴 때 만든다): %s", upload_dir)
    if FFMPEG_PATH:
        log.info("영상 변환 프로그램: %s", FFMPEG_PATH)
    else:
        log.warning("ffmpeg 를 찾지 못했습니다 — 고객 영상은 FHD 로 줄이지 않고 원본 그대로 저장됩니다(backend/.env 의 FFMPEG_PATH)")
    yield


def create_app(frontend_dir: Optional[Path] = FRONTEND_DIR) -> FastAPI:
    app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.include_router(portal.router)

    # 고객 주소로는 /api/portal/ 만 연다. 그 밖의 /api/... 는 화면 파일이 대신 나가지 않게 여기서 404 로 끝낸다
    @app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def no_other_api(rest: str = ""):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    # 화면 파일 (맨 마지막 — API 보다 뒤)
    if frontend_dir is not None and Path(frontend_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    else:
        log.warning("화면 파일 폴더가 없습니다: %s", frontend_dir)
    return app


app = create_app()
