"""A/S 영상 받기 · FHD 변환 (2026-11-24 사용자 결정).

흐름: 고객이 올린 원본을 서버 작업 폴더에 받는다 → 원본을 NAS 사진 폴더에 바로 저장(접수 화면은 기다리지 않는다)
     → 뒤에서 ffmpeg 로 FHD(가로세로 1920×1080 안) H.264 MP4 로 바꾼다 → 바뀐 파일로 행을 갈아 끼우고 원본은 지운다.
- ffmpeg 가 없거나 변환에 실패하면 원본을 그대로 둔다 — 접수 증거가 없어지지 않게.
- 행을 고치지 않고 '새 행 추가 + 옛 행 삭제'로 바꾼다: 이 사이트의 DB 계정은 as_case_photo 에 UPDATE 권한이 없다(preflight).
- 변환은 한 번에 하나씩(같은 서버에 SQL Server 가 돈다) · 낮은 우선순위로 돌린다.
- 폴더 규칙은 사진과 같다: <AS_UPLOAD_DIR>/<접수 id>/<uuid>.<확장자> — 직원 화면(crm-rma)이 같은 폴더에서 읽는다.
"""
import logging
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ..config import AS_UPLOAD_DIR, FFMPEG_PATH, VIDEO_MAX_BYTES, VIDEO_TIMEOUT_SEC, VIDEO_WORK_DIR
from ..models import AsCasePhoto

log = logging.getLogger(__name__)

CUSTOMER_VIDEO_MAX = 3
STORAGE_DOWN = "영상을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요"
NOT_VIDEO = "사진(JPG·PNG) 또는 영상(MP4·MOV) 파일만 올릴 수 있습니다"

# ftyp 상자로 시작하지만 영상이 아닌 것 (아이폰 사진 HEIC · AVIF 등)
_IMAGE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video")


def is_video(content_type) -> bool:
    return bool(content_type) and str(content_type).startswith("video/")


def sniff_video(head: bytes):
    """파일 앞부분으로 (content_type, 확장자) 를 돌려준다. 영상이 아니면 None."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _IMAGE_BRANDS:
            return None
        if brand.startswith(b"qt"):
            return "video/quicktime", ".mov"
        if brand.startswith(b"3g"):
            return "video/3gpp", ".3gp"
        return "video/mp4", ".mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", ".webm"
    return None


def _video_query(db, case_id, by):
    return db.query(AsCasePhoto).filter(
        AsCasePhoto.case_id == case_id, AsCasePhoto.uploaded_by == by, AsCasePhoto.content_type.like("video/%"))


def max_mb() -> int:
    return VIDEO_MAX_BYTES // (1024 * 1024)


def too_big_message() -> str:
    return f"영상 한 개는 {max_mb():,}MB 이하여야 합니다"


def work_file() -> Path:
    VIDEO_WORK_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEO_WORK_DIR / f"{uuid4().hex}.part"


def store_original(db, case, src: Path, orig_name: str, by: str, by_name: str) -> AsCasePhoto:
    """작업 폴더의 원본(src)을 NAS 사진 폴더에 복사하고 행을 더한다(flush). src 는 지우지 않는다(변환에 쓴다)."""
    with open(src, "rb") as f:
        head = f.read(16)
    kind = sniff_video(head)
    if not kind:
        raise HTTPException(400, NOT_VIDEO)
    size = Path(src).stat().st_size
    if size > VIDEO_MAX_BYTES:
        raise HTTPException(400, too_big_message())
    if _video_query(db, case.id, by).count() >= CUSTOMER_VIDEO_MAX:
        raise HTTPException(400, f"영상은 최대 {CUSTOMER_VIDEO_MAX}개까지 올릴 수 있습니다")
    content_type, ext = kind
    stored_name = f"{uuid4().hex}{ext}"
    dest = Path(AS_UPLOAD_DIR) / f"{case.id}" / stored_name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    except OSError:
        log.exception("A/S 영상 저장 실패 case_id=%s", case.id)
        _unlink(dest)
        raise HTTPException(503, STORAGE_DOWN)
    row = AsCasePhoto(case_id=case.id, stored_name=stored_name, orig_name=(orig_name or "")[:120],
                      content_type=content_type, size=size, uploaded_by=by, uploaded_by_name=by_name)
    db.add(row)
    db.flush()
    return row


def ffmpeg_cmd(ffmpeg: str, src: Path, dst: Path) -> list:
    """FHD(가로 영상 1920×1080, 세로 영상 1080×1920 안)로 줄인 H.264/AAC MP4. 작은 영상은 키우지 않는다."""
    scale = ("scale=w='min(iw,if(gte(iw,ih),1920,1080))':h='min(ih,if(gte(iw,ih),1080,1920))'"
             ":force_original_aspect_ratio=decrease:force_divisible_by=2")
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", scale, "-fpsmax", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-map_metadata", "-1",            # 촬영 위치(GPS) 같은 정보는 남기지 않는다
        "-movflags", "+faststart",        # 다 받기 전에 재생이 시작되게
        "-threads", "2",
        "-f", "mp4", str(dst),
    ]


def _run(cmd, timeout) -> bool:
    flags = 0
    if sys.platform == "win32":   # 창 없이 · 낮은 우선순위로 (같은 서버의 SQL Server · 다른 사이트를 방해하지 않게)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        log.warning("영상 변환 시간 초과(%s초) — 원본을 그대로 둔다", timeout)
        return False
    except OSError:
        log.exception("ffmpeg 를 실행하지 못했습니다: %s", cmd[0])
        return False
    if r.returncode != 0:
        tail = (r.stderr or b"")[-800:].decode("utf-8", "replace")
        log.warning("영상 변환 실패(코드 %s) — 원본을 그대로 둔다: %s", r.returncode, tail)
        return False
    return True


def _unlink(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        log.warning("파일을 지우지 못했습니다: %s", path)


def convert_video(photo_id: int, src: Path, session_factory) -> None:
    """뒤에서 도는 변환. 어떤 경우에도 예외를 밖으로 내지 않고, 작업 폴더의 파일(src · 결과)은 지운다."""
    out = Path(VIDEO_WORK_DIR) / f"{uuid4().hex}.mp4"
    try:
        if not FFMPEG_PATH:
            log.warning("ffmpeg 가 없어 영상을 원본 그대로 둡니다 photo_id=%s", photo_id)
            return
        if not (_run(ffmpeg_cmd(FFMPEG_PATH, Path(src), out), VIDEO_TIMEOUT_SEC) and out.is_file() and out.stat().st_size > 0):
            return
        db = session_factory()
        dest = None
        try:
            old = db.get(AsCasePhoto, photo_id)
            if old is None:      # 변환하는 사이 고객이 지웠다
                return
            old_path = Path(AS_UPLOAD_DIR) / f"{old.case_id}" / old.stored_name
            dest = Path(AS_UPLOAD_DIR) / f"{old.case_id}" / f"{uuid4().hex}.mp4"
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(out, dest)
            except OSError:
                log.exception("변환한 영상을 저장하지 못했습니다 — 원본을 그대로 둔다 photo_id=%s", photo_id)
                _unlink(dest)
                return
            new = AsCasePhoto(case_id=old.case_id, stored_name=dest.name, orig_name=old.orig_name,
                              content_type="video/mp4", size=dest.stat().st_size, uploaded_by=old.uploaded_by,
                              uploaded_by_name=old.uploaded_by_name, created_at=old.created_at)
            db.add(new)
            db.delete(old)
            db.commit()
            _unlink(old_path)
            log.info("영상 변환 완료 photo_id=%s → %s", photo_id, new.id)
        except Exception:
            db.rollback()
            log.exception("영상 변환 결과를 반영하지 못했습니다 — 원본을 그대로 둔다 photo_id=%s", photo_id)
            if dest is not None:
                _unlink(dest)
        finally:
            db.close()
    except Exception:
        log.exception("영상 변환 중 오류 photo_id=%s", photo_id)
    finally:
        _unlink(src)
        _unlink(out)


def schedule(photo_id: int, src: Path) -> None:
    from ..database import SessionLocal
    _executor.submit(convert_video, photo_id, Path(src), SessionLocal)
