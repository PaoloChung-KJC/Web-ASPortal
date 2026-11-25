"""A/S 영상 받기 · 1분 구간 자르기.

2026-11-24 처음에는 서버에서 FHD 로 다시 압축(변환)했으나 느리고(16초 영상에 17분) 복잡해서
2026-11-25 사용자 결정으로 뺐다. 지금 규칙:
- 1분 이하 영상: 원본 그대로 저장 (ffmpeg 를 쓰지 않는다)
- 1분이 넘는 영상: 고객이 화면에서 최대 1분 구간을 고른다(trim_start · trim_end) → 서버가 ffmpeg 로
  그 구간만 '다시 압축하지 않고'(-c copy) 떼어 저장한다. 1~2초면 끝나고 화질이 그대로다. 요청 안에서 끝낸다.
- ffmpeg 가 없거나 자르기에 실패하면 원본 전체를 그대로 둔다 — 접수 증거가 없어지지 않게.
- -c copy 는 키프레임에서만 자를 수 있어 시작이 고른 곳보다 몇 초 앞당겨질 수 있다.
- 폴더 규칙은 사진과 같다: <AS_UPLOAD_DIR>/<접수 id>/<uuid>.<확장자> — 직원 화면(crm-rma)이 같은 폴더에서 읽는다.
"""
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ..config import AS_UPLOAD_DIR, FFMPEG_PATH, VIDEO_MAX_BYTES, VIDEO_WORK_DIR
from ..models import AsCasePhoto

log = logging.getLogger(__name__)

CUSTOMER_VIDEO_MAX = 3
TRIM_MAX_SEC = 60          # 영상 길이 한도 (사용자 결정 2026-11-25) — 화면 portal.jsx VIDEO_MAX_SEC 와 같게
TRIM_TIMEOUT_SEC = 180     # 자르기(복사)는 보통 몇 초 — 이보다 길면 원본 그대로
STORAGE_DOWN = "영상을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요"
NOT_VIDEO = "사진(JPG·PNG) 또는 영상(MP4·MOV) 파일만 올릴 수 있습니다"

# ftyp 상자로 시작하지만 영상이 아닌 것 (아이폰 사진 HEIC · AVIF 등)
_IMAGE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}



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
    """작업 폴더의 원본(src)을 NAS 사진 폴더에 복사하고 행을 더한다(flush). src 는 지우지 않는다(부른 쪽이 지운다)."""
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


def trim_span(start, end) -> tuple:
    """(시작 초, 길이 초). 길이는 1분을 넘지 않는다. 끝이 없거나 시작보다 앞이면 1분."""
    s = max(0.0, float(start or 0))
    length = float(TRIM_MAX_SEC)
    if end is not None and float(end) > s:
        length = min(length, float(end) - s)
    return s, length


def trim_cmd(ffmpeg: str, src: Path, dst: Path, start: float, length: float) -> list:
    """다시 압축하지 않고(-c copy) start 부터 length 초만 떼어 낸다. 입력 앞 -ss 는 가까운 키프레임으로 빠르게 간다."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{float(start):.3f}", "-i", str(src), "-t", f"{float(length):.3f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
        "-avoid_negative_ts", "make_zero",
    ]
    if Path(dst).suffix.lower() in (".mp4", ".mov", ".3gp", ".m4v"):
        cmd += ["-movflags", "+faststart"]      # 다 받기 전에 재생이 시작되게 (mp4 계열에만 있는 옵션)
    return cmd + [str(dst)]


def trim(src: Path, start, end, ext: str):
    """src 의 고른 구간을 작업 폴더의 새 파일로 떼어 내 그 경로를 돌려준다. 못 하면 None(결과 조각은 지운다)."""
    if not FFMPEG_PATH:
        log.warning("ffmpeg 가 없어 영상을 자르지 못하고 원본 그대로 둡니다")
        return None
    s, length = trim_span(start, end)
    out = Path(VIDEO_WORK_DIR) / f"{uuid4().hex}{ext}"
    began = time.monotonic()
    ok = _run(trim_cmd(FFMPEG_PATH, Path(src), out, s, length), TRIM_TIMEOUT_SEC)
    if not ok or not out.is_file() or out.stat().st_size == 0:
        _unlink(out)
        return None
    log.info("영상 자르기 %.1f초부터 %.1f초 · %.1fMB → %.1fMB · %.1f초 걸림", s, length,
             Path(src).stat().st_size / 1048576, out.stat().st_size / 1048576, time.monotonic() - began)
    return out


def _run(cmd, timeout) -> bool:
    flags = 0
    if sys.platform == "win32":   # 창 없이 · 낮은 우선순위로 (같은 서버의 SQL Server · 다른 사이트를 방해하지 않게)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        log.warning("영상 자르기 시간 초과(%s초) — 원본을 그대로 둔다", timeout)
        return False
    except OSError:
        log.exception("ffmpeg 를 실행하지 못했습니다: %s", cmd[0])
        return False
    if r.returncode != 0:
        tail = (r.stderr or b"")[-800:].decode("utf-8", "replace")
        log.warning("영상 자르기 실패(코드 %s) — 원본을 그대로 둔다: %s", r.returncode, tail)
        return False
    return True


def _unlink(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        log.warning("파일을 지우지 못했습니다: %s", path)
