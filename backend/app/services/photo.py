"""A/S 사진 검사·저장. 고객(외부)이 올리므로 내용 시그니처로 형식을 확인하고 파일명은 서버가 만든다.

폴더 규칙은 crm-rma services/as_photo.py 와 같아야 한다 — 직원 화면이 같은 폴더(NAS)에서 읽는다:
  <AS_UPLOAD_DIR>/<접수 id>/<uuid>.<확장자>     지운 사진은 <AS_UPLOAD_DIR>/_trash/
"""
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ..config import AS_UPLOAD_DIR  # 테스트에서 이 모듈의 AS_UPLOAD_DIR 을 바꿔 쓴다
from ..models import AsCasePhoto

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024
CUSTOMER_MAX = 10
_LIMITS = {"customer": CUSTOMER_MAX}
STORAGE_DOWN = "사진을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요"


def sniff_image(data: bytes):
    """파일 앞부분 시그니처로 (content_type, 확장자) 를 돌려준다. 이미지가 아니면 None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def save_photo(db, case, data: bytes, orig_name: str, by: str, by_name: str) -> AsCasePhoto:
    """파일을 먼저 쓰고 성공하면 행을 추가한다(flush). 커밋은 호출한 쪽.

    사진 폴더(NAS 공유 폴더)에 쓰지 못하면 503 — 접수 건과 DB 는 건드리지 않는다.
    """
    kind = sniff_image(data)
    if not kind:
        raise HTTPException(400, "사진 파일(JPG·PNG)만 올릴 수 있습니다")
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "사진 한 장은 8MB 이하여야 합니다")
    limit = _LIMITS[by]
    count = db.query(AsCasePhoto).filter(AsCasePhoto.case_id == case.id, AsCasePhoto.uploaded_by == by).count()
    if count >= limit:
        raise HTTPException(400, f"사진은 최대 {limit}장까지 올릴 수 있습니다")
    content_type, ext = kind
    stored_name = f"{uuid4().hex}{ext}"
    path = Path(AS_UPLOAD_DIR) / f"{case.id}" / stored_name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        log.exception("A/S 사진 저장 실패 case_id=%s", case.id)
        raise HTTPException(503, STORAGE_DOWN)
    photo = AsCasePhoto(case_id=case.id, stored_name=stored_name, orig_name=(orig_name or "")[:120],
                        content_type=content_type, size=len(data), uploaded_by=by, uploaded_by_name=by_name)
    db.add(photo)
    db.flush()
    return photo


def photo_path(photo) -> Path:
    return Path(AS_UPLOAD_DIR) / f"{photo.case_id}" / photo.stored_name


def trash_photo(photo) -> None:
    """파일을 지우지 않고 _trash 로 옮긴다. 파일이 없거나 옮기지 못해도 예외를 내지 않는다."""
    src = photo_path(photo)
    try:
        if not src.exists():
            return
        dest_dir = Path(AS_UPLOAD_DIR) / "_trash"
        dest_dir.mkdir(parents=True, exist_ok=True)
        src.replace(dest_dir / photo.stored_name)
    except OSError:
        log.exception("A/S 사진 휴지통 이동 실패 photo_id=%s", getattr(photo, "id", None))
