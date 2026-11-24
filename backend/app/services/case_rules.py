"""고객용 상태·권한·응답 변환과 입력 도우미.

crm-rma 의 services/as_case.py(고객용 부분)와 routers/as_case.py 의 도우미를 옮긴 것.
규칙을 바꿀 때는 crm-rma 쪽과 같이 바꾼다 — 직원 화면과 고객 화면이 같은 상태를 다르게 읽으면 안 된다.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AsCase, CommonCode, CommonCodeGroup

REV_SCOPE = "as.case"      # record_revision 의 구분 — 직원 화면과 같은 값
SYMPTOM_MAX = 4000

CUSTOMER_STATUS_LABEL = {"received": "접수됨", "progress": "처리 중", "done": "완료", "canceled": "취소됨"}

# 고객에게 내보내도 되는 칸 (이 목록에 없는 칸은 절대 내보내지 않는다)
# method = 접수 구분(공통코드 AS_KIND: CS 현장 / AS 입고 수리). 2026-11-19 부터 고객이 접수할 때 직접 고른다
PORTAL_FIELDS = (
    "id", "as_no", "received_date", "reporter_name", "reporter_phone", "method", "product_group",
    "model_text", "qty", "symptom", "site_name", "site_address", "site_phone", "customer_notice",
)

FIELD_LABELS = {
    "reporter_name": "접수자", "reporter_phone": "접수자 연락처", "model_text": "모델명",
    "site_name": "장소", "site_address": "장소(주소)", "site_phone": "장소 연락처",
}

_KST = timezone(timedelta(hours=9))


def customer_status(status, canceled=False):
    """고객에게 보여 줄 단순 상태: received / progress(open·hold) / done(done·closed).

    고객이 직접 취소해 닫힌 건(closed + canceled)은 "완료"가 아니라 canceled(취소됨)로 보여 준다.
    """
    if status == "received":
        return "received"
    if status in ("open", "hold"):
        return "progress"
    if status == "closed" and canceled:
        return "canceled"
    return "done"


def customer_can_edit(status):
    return status == "received"


def customer_can_add_photo(status):
    return status in ("received", "open", "hold")


def portal_view(case, photos):
    """고객용 응답: 허용 칸 + 고객 상태 + 권한 + 고객이 올린 사진만."""
    view = {f: getattr(case, f, None) for f in PORTAL_FIELDS}
    status = getattr(case, "status", None)
    cs = customer_status(status, getattr(case, "canceled_at", None) is not None)
    view["status"] = cs
    view["status_label"] = CUSTOMER_STATUS_LABEL[cs]
    view["can_edit"] = customer_can_edit(status)
    view["can_add_photo"] = customer_can_add_photo(status)
    # kind: photo / video (2026-11-24 영상 추가)
    view["photos"] = [
        {"id": p.id, "orig_name": p.orig_name,
         "kind": "video" if str(getattr(p, "content_type", "") or "").startswith("video/") else "photo"}
        for p in photos
        if getattr(p, "uploaded_by", None) == "customer"
    ]
    return view


def today_kst() -> date:
    return datetime.now(_KST).date()


def check_text_lengths(data: dict, fields: Iterable[str]) -> None:
    """글자 칸이 DB 칸 길이를 넘으면 400 (PostgreSQL 은 넘치면 저장 오류가 난다)."""
    for f in fields:
        v = data.get(f)
        limit = AsCase.__table__.c[f].type.length
        if isinstance(v, str) and limit and len(v.strip()) > limit:
            raise HTTPException(400, f"{FIELD_LABELS.get(f, f)}는 {limit:,}자까지 입력할 수 있습니다")


def code_names(db: Session, group_code: str) -> dict:
    rows = (db.query(CommonCode.code, CommonCode.display_name)
            .join(CommonCodeGroup, CommonCodeGroup.id == CommonCode.group_id)
            .filter(CommonCodeGroup.group_code == group_code).all())
    return {c: n for c, n in rows}
