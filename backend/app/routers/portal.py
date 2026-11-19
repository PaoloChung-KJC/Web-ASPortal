"""고객 A/S API (/api/portal) — 이 프로그램이 내보내는 유일한 API.

crm-rma routers/as_portal.py 를 옮긴 것(주소·응답·문구 동일).
고객은 자기 거래처(company_id)의 건만 보고 고친다. 다른 거래처의 건·사진은 있는지조차 알리지 않고 404.
응답은 services/case_rules.py 의 허용 칸 목록으로만 만든다(직원용 칸은 내보내지 않는다).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AsCase, AsCaseLog, AsCasePhoto, AsPortalAccount, CommonCode, CommonCodeGroup, RecordRevision
from ..services import photo as as_photo
from ..services.case_rules import (
    REV_SCOPE, SYMPTOM_MAX, check_text_lengths, code_names, customer_can_add_photo, customer_can_edit,
    portal_view, today_kst,
)
from ..services.client_ip import client_ip
from ..services.numbering import next_as_no
from ..services.portal_auth import (
    authenticate, check_login_rate, create_portal_token, get_portal_account, set_password, verify_password,
)

router = APIRouter(prefix="/api/portal", tags=["portal"])

PAGE_SIZE = 20
QTY_MAX = 9999
CLIENT_KEY_MAX = 40
CASE_NOT_FOUND = "접수 건을 찾을 수 없습니다"
PHOTO_NOT_FOUND = "사진을 찾을 수 없습니다"
ALREADY_CONFIRMED = "이미 확인된 접수입니다. 변경은 담당자에게 문의해 주세요"
PRODUCT_GROUP = "AS_PRODUCT_GROUP"

# 고객이 고칠 수 있는 글자 칸 (길이 검사 대상)
_TEXT_FIELDS = ("reporter_name", "reporter_phone", "model_text", "site_name", "site_address", "site_phone")
_OPTIONAL_TEXT = ("model_text", "site_name", "site_address", "site_phone")
_FILTERS = {"all": None, "progress": ("received", "open", "hold"), "done": ("done", "closed")}


def get_active_account(account: AsPortalAccount = Depends(get_portal_account)) -> AsPortalAccount:
    """비밀번호를 바꾸기 전에는 login·me·password 외 모든 기능을 막는다."""
    if account.must_change_pw == "Y":
        raise HTTPException(403, "비밀번호를 먼저 변경해 주세요")
    return account


def _account_dict(a: AsPortalAccount) -> dict:
    return {"login_id": a.login_id, "company_name": a.company.name if a.company else None,
            "must_change_pw": a.must_change_pw == "Y"}


def _get_case(db: Session, account: AsPortalAccount, case_id: int) -> AsCase:
    c = db.query(AsCase).filter(AsCase.id == case_id, AsCase.company_id == account.company_id).first()
    if not c:
        raise HTTPException(404, CASE_NOT_FOUND)
    return c


def _detail(db: Session, c: AsCase) -> dict:
    d = portal_view(c, c.photos)
    names = code_names(db, PRODUCT_GROUP)
    d["product_group_name"] = names.get(c.product_group, c.product_group) if c.product_group else None
    return d


def _bump_case_rev(db: Session, case_id: int, reporter_name: Optional[str]) -> None:
    """고객 수정·취소도 직원 화면의 동시 저장 확인에 걸리도록 as.case 수정 번호를 올린다. 커밋은 호출한 쪽."""
    key = str(case_id)
    row = db.query(RecordRevision).filter_by(scope=REV_SCOPE, ref_key=key).with_for_update().first()
    if row is None:
        try:
            with db.begin_nested():
                db.add(RecordRevision(scope=REV_SCOPE, ref_key=key, rev=0))
                db.flush()
        except IntegrityError:
            pass  # 동시에 만든 쪽이 있으면 다시 읽는다
        row = db.query(RecordRevision).filter_by(scope=REV_SCOPE, ref_key=key).with_for_update().first()
    row.rev = int(row.rev or 0) + 1
    row.updated_by = "portal"
    row.updated_by_name = f"고객:{reporter_name or ''}"[:50]
    row.updated_at = datetime.now(timezone.utc)


# ---- 입력 검증 ----

def _str(data: dict, f: str) -> Optional[str]:
    v = data.get(f)
    if v is None:
        return None
    if not isinstance(v, str):
        raise HTTPException(400, "입력값 형식이 올바르지 않습니다")
    return v.strip()


def _clean_qty(v) -> int:
    ok = isinstance(v, int) and not isinstance(v, bool)
    if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        v, ok = int(v.strip()), True
    if not ok or not 1 <= v <= QTY_MAX:
        raise HTTPException(400, "수량을 확인해 주세요")
    return v


def _clean_symptom(v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise HTTPException(400, "증상을 입력해 주세요")
    s = v.strip()
    if len(s) > SYMPTOM_MAX:
        raise HTTPException(400, "증상은 4,000자까지 입력할 수 있습니다")
    return s


def _clean_group(db: Session, v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise HTTPException(400, "제품군을 선택해 주세요")
    v = v.strip()
    if v not in code_names(db, PRODUCT_GROUP):
        raise HTTPException(400, "제품군을 확인해 주세요")
    return v


def _apply(db: Session, c: AsCase, data: dict, creating: bool) -> None:
    """허용된 칸만 검증해 반영한다. 수정 때는 키가 있는 칸만 건드린다."""
    for f in _TEXT_FIELDS:
        _str(data, f)  # 문자열이 아니면 400
    check_text_lengths(data, _TEXT_FIELDS)
    for f, label in (("reporter_name", "접수자 이름을"), ("reporter_phone", "접수자 연락처를")):
        if creating or f in data:
            v = _str(data, f)
            if not v:
                raise HTTPException(400, f"{label} 입력해 주세요")
            setattr(c, f, v)
    if creating or "product_group" in data:
        c.product_group = _clean_group(db, data.get("product_group"))
    if creating or "qty" in data:
        c.qty = _clean_qty(data.get("qty"))
    if creating or "symptom" in data:
        c.symptom = _clean_symptom(data.get("symptom"))
    for f in _OPTIONAL_TEXT:
        if f in data:
            setattr(c, f, _str(data, f) or None)


def _client_key(data: dict) -> Optional[str]:
    v = data.get("client_key")
    if v in (None, ""):
        return None
    if not isinstance(v, str) or len(v) > CLIENT_KEY_MAX:
        raise HTTPException(400, f"client_key 는 {CLIENT_KEY_MAX}자까지 입니다")
    return v


def _existing_by_key(db: Session, account: AsPortalAccount, key: Optional[str]) -> Optional[AsCase]:
    if not key:
        return None
    c = db.query(AsCase).filter_by(portal_account_id=account.id, client_key=key).first()
    if c is not None and c.company_id != account.company_id:
        # 직원이 다른 거래처로 옮긴 건: 내용은 돌려주지 않는다 (GET 이 404 인 것과 같은 격리)
        raise HTTPException(409, "이미 처리된 접수 요청입니다. 접수 목록을 확인해 주세요")
    return c


# ---- 상태 확인 (로그인·DB 불필요 — 설치 뒤 프로그램이 떴는지 보는 용도) ----

@router.get("/health")
def health():
    return {"status": "ok"}


# ---- 로그인·계정 ----

@router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    check_login_rate()  # 막힌 요청은 bcrypt 비교까지 가지 않는다
    acc = authenticate(db, str(payload.get("login_id") or ""), str(payload.get("password") or ""),
                       ip=client_ip(request))
    return {"token": create_portal_token(acc), "account": _account_dict(acc)}


@router.get("/me")
def me(account: AsPortalAccount = Depends(get_portal_account)):
    return _account_dict(account)


@router.post("/password")
def change_password(payload: dict, db: Session = Depends(get_db),
                    account: AsPortalAccount = Depends(get_portal_account)):
    cur, new = payload.get("current"), payload.get("new")
    if not isinstance(cur, str) or not verify_password(cur, account.hashed_password):
        raise HTTPException(400, "현재 비밀번호가 올바르지 않습니다")
    set_password(db, account, new if isinstance(new, str) else "", must_change=False)
    return {"token": create_portal_token(account)}  # 토큰 번호가 올라갔으므로 새 토큰


# ---- 선택 목록 ----

@router.get("/options")
def options(db: Session = Depends(get_db), account: AsPortalAccount = Depends(get_active_account)):
    rows = (db.query(CommonCode).join(CommonCodeGroup, CommonCodeGroup.id == CommonCode.group_id)
            .filter(CommonCodeGroup.group_code == PRODUCT_GROUP, CommonCode.use_yn == "Y")
            .order_by(CommonCode.sort_order, CommonCode.id).all())
    seen, reporters = set(), []
    recent = (db.query(AsCase.reporter_name, AsCase.reporter_phone).filter(AsCase.company_id == account.company_id)
              .order_by(AsCase.id.desc()).limit(200).all())
    for name, phone in recent:
        if not name or (name, phone) in seen:
            continue
        seen.add((name, phone))
        reporters.append({"name": name, "phone": phone})
        if len(reporters) == 5:
            break
    return {"product_groups": [{"code": r.code, "name": r.display_name} for r in rows],
            "recent_reporters": reporters}


# ---- 접수 건 ----

@router.get("/cases")
def list_cases(filter: str = "all", offset: int = 0, db: Session = Depends(get_db),
               account: AsPortalAccount = Depends(get_active_account)):
    if filter not in _FILTERS:
        raise HTTPException(400, "filter 는 all, progress, done 중 하나입니다")
    qry = db.query(AsCase).filter(AsCase.company_id == account.company_id)
    if _FILTERS[filter]:
        qry = qry.filter(AsCase.status.in_(_FILTERS[filter]))
    rows = (qry.order_by(AsCase.received_date.desc(), AsCase.id.desc())
            .offset(max(offset, 0)).limit(PAGE_SIZE + 1).all())
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    names = code_names(db, PRODUCT_GROUP)
    counts = {}
    if rows:
        counts = dict(db.query(AsCasePhoto.case_id, func.count(AsCasePhoto.id))
                      .filter(AsCasePhoto.case_id.in_([c.id for c in rows]), AsCasePhoto.uploaded_by == "customer")
                      .group_by(AsCasePhoto.case_id).all())
    out = []
    for c in rows:
        v = portal_view(c, [])
        out.append({
            "id": c.id, "as_no": c.as_no, "received_date": c.received_date, "product_group": c.product_group,
            "product_group_name": names.get(c.product_group, c.product_group) if c.product_group else None,
            "symptom_head": (c.symptom or "")[:40], "status": v["status"], "status_label": v["status_label"],
            "photo_count": counts.get(c.id, 0),
        })
    return {"rows": out, "has_more": has_more}


@router.post("/cases")
def create_case(payload: dict, db: Session = Depends(get_db),
                account: AsPortalAccount = Depends(get_active_account)):
    key = _client_key(payload)
    dup = _existing_by_key(db, account, key)
    if dup:
        return _detail(db, dup)
    today = today_kst()
    for attempt in range(2):  # as_no 유일 제약에 걸리면 한 번 다시 매긴다
        c = AsCase(source="portal", status="received", received_date=today.isoformat(),
                   company_id=account.company_id, portal_account_id=account.id, client_key=key)
        _apply(db, c, payload, creating=True)
        c.created_by_name = c.updated_by_name = f"고객:{c.reporter_name}"[:60]
        c.as_no = next_as_no(db, today)
        try:
            with db.begin_nested():
                db.add(c)
                db.flush()
            break
        except IntegrityError:
            dup = _existing_by_key(db, account, key)  # 같은 client_key 로 동시에 들어온 경우
            if dup:
                return _detail(db, dup)
            if attempt:
                raise HTTPException(409, "접수 번호를 매기지 못했습니다. 다시 시도해 주세요")
    db.commit()
    return _detail(db, c)


@router.get("/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db), account: AsPortalAccount = Depends(get_active_account)):
    return _detail(db, _get_case(db, account, case_id))


def _lock_editable(db: Session, c: AsCase) -> None:
    """수정 번호 줄을 먼저 잠근 뒤 상태를 다시 읽어 확인한다 — 그 사이 직원 확인이 끼어들 수 없게."""
    _bump_case_rev(db, c.id, c.reporter_name)
    db.refresh(c)
    if not customer_can_edit(c.status):
        db.rollback()  # 올린 수정 번호는 남기지 않는다
        raise HTTPException(409, ALREADY_CONFIRMED)


@router.put("/cases/{case_id}")
def update_case(case_id: int, payload: dict, db: Session = Depends(get_db),
                account: AsPortalAccount = Depends(get_active_account)):
    c = _get_case(db, account, case_id)
    _lock_editable(db, c)
    try:
        _apply(db, c, payload, creating=False)
    except HTTPException:
        db.rollback()  # 입력 오류면 올린 수정 번호도 되돌린다
        raise
    c.updated_by_name = f"고객:{c.reporter_name}"[:60]
    db.commit()
    return _detail(db, c)


@router.post("/cases/{case_id}/cancel")
def cancel_case(case_id: int, db: Session = Depends(get_db), account: AsPortalAccount = Depends(get_active_account)):
    c = _get_case(db, account, case_id)
    _lock_editable(db, c)
    db.add(AsCaseLog(case_id=c.id, log_date=today_kst().isoformat(), kind="CANCEL",
                     content=f"고객이 접수를 취소했습니다 ({c.reporter_name or ''})",
                     created_by_name=f"고객:{c.reporter_name or ''}"[:60]))
    c.status = "closed"
    c.closed_at = datetime.now(timezone.utc)
    c.canceled_at = c.closed_at   # 고객 화면에 "완료"가 아니라 "취소됨"으로 보이게 하는 표시
    c.updated_by_name = f"고객:{c.reporter_name}"[:60]
    db.commit()
    return _detail(db, c)


# ---- 사진 ----

@router.post("/cases/{case_id}/photos")
def upload_photo(case_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                 account: AsPortalAccount = Depends(get_active_account)):
    c = _get_case(db, account, case_id)
    if not customer_can_add_photo(c.status):
        raise HTTPException(409, "사진을 더 올릴 수 없는 접수입니다")
    data = file.file.read(as_photo.MAX_BYTES + 1)
    p = as_photo.save_photo(db, c, data, file.filename or "", "customer", f"고객:{c.reporter_name or ''}"[:60])
    db.commit()
    return {"id": p.id, "orig_name": p.orig_name}


def _customer_photo(db: Session, c: AsCase, pid: int) -> AsCasePhoto:
    p = db.get(AsCasePhoto, pid)
    if not p or p.case_id != c.id or p.uploaded_by != "customer":
        raise HTTPException(404, PHOTO_NOT_FOUND)
    return p


@router.delete("/cases/{case_id}/photos/{pid}")
def delete_photo(case_id: int, pid: int, db: Session = Depends(get_db),
                 account: AsPortalAccount = Depends(get_active_account)):
    c = _get_case(db, account, case_id)
    p = _customer_photo(db, c, pid)
    if not customer_can_edit(c.status):
        raise HTTPException(409, ALREADY_CONFIRMED)
    as_photo.trash_photo(p)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/photos/{pid}")
def get_photo(pid: int, db: Session = Depends(get_db), account: AsPortalAccount = Depends(get_active_account)):
    p = db.get(AsCasePhoto, pid)
    c = db.query(AsCase).filter(AsCase.id == p.case_id, AsCase.company_id == account.company_id).first() if p else None
    if not p or not c or p.uploaded_by != "customer":
        raise HTTPException(404, PHOTO_NOT_FOUND)
    path = as_photo.photo_path(p)
    if not path.is_file():
        raise HTTPException(404, PHOTO_NOT_FOUND)
    # 올린 사람이 정한 파일명은 머리글·경로에 쓰지 않는다
    return FileResponse(path, media_type=p.content_type, headers={"X-Content-Type-Options": "nosniff"})
