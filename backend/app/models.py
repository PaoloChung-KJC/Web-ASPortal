"""고객 A/S 사이트가 쓰는 표만. 칸 이름·형식은 crm-rma/backend/app/models 와 같아야 한다.

crm-rma 와 다른 점은 셋뿐이다 (tests/test_crm_schema_match.py 가 나머지를 대조한다):
  1. company 는 id · name 두 칸만 둔다 (읽기 전용)
  2. as_case.item_id · rma_id 는 외래키 선언 없는 정수 칸 (item · rma 표를 여기서 정의하지 않는다)
  3. 그 밖의 표는 정의하지 않는다
시각 칸은 모두 timezone=True (PostgreSQL 9시간 오차 방지).
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from .database import Base


class Company(Base):
    __tablename__ = "company"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)


class CommonCodeGroup(Base):
    __tablename__ = "common_code_group"
    id = Column(Integer, primary_key=True)
    group_code = Column(String(40), unique=True, nullable=False)
    group_name = Column(String(80), nullable=False)
    codes = relationship("CommonCode", back_populates="group")


class CommonCode(Base):
    __tablename__ = "common_code"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("common_code_group.id"), nullable=False)
    code = Column(String(40), nullable=False)
    display_name = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    use_yn = Column(String(1), default="Y")
    group = relationship("CommonCodeGroup", back_populates="codes")


class AutoNumberRule(Base):
    __tablename__ = "auto_number_rule"
    id = Column(Integer, primary_key=True)
    doc_code = Column(String(40), unique=True, nullable=False)
    doc_name = Column(String(120), nullable=False)
    auto_yn = Column(String(1), default="Y")
    text_prefix = Column(String(20), default="")
    date_format = Column(String(20), default="YYMMDD")
    number_length = Column(Integer, default=10)
    use_yn = Column(String(1), default="Y")


class RecordRevision(Base):
    """수정 번호 표 — 직원 화면과 동시 저장 확인을 맞춘다 (scope "as.case")."""
    __tablename__ = "record_revision"
    scope = Column(String(40), primary_key=True)
    ref_key = Column(String(60), primary_key=True)
    rev = Column(Integer, nullable=False, default=0)
    updated_by = Column(String(50))
    updated_by_name = Column(String(50))
    updated_at = Column(DateTime(timezone=True))


class AsPortalAccount(Base):
    __tablename__ = "as_portal_account"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    login_id = Column(String(30), nullable=False, unique=True)
    hashed_password = Column(String(255), nullable=False)
    label = Column(String(120))
    use_yn = Column(String(1), nullable=False, default="Y")
    must_change_pw = Column(String(1), nullable=False, default="Y")
    fail_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True))
    token_ver = Column(Integer, nullable=False, default=0)
    last_login_at = Column(DateTime(timezone=True))
    last_login_ip = Column(String(64))
    pw_changed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by_name = Column(String(60))

    company = relationship("Company")


class AsCase(Base):
    __tablename__ = "as_case"
    __table_args__ = (UniqueConstraint("portal_account_id", "client_key", name="uq_as_case_account_client_key"),)

    id = Column(Integer, primary_key=True)
    as_no = Column(String(16), nullable=False, unique=True, index=True)
    source = Column(String(10), nullable=False, default="staff")  # portal / staff
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False, index=True)
    portal_account_id = Column(Integer, ForeignKey("as_portal_account.id"), index=True)
    client_key = Column(String(40))
    status = Column(String(10), nullable=False, default="received", index=True)  # received/open/hold/done/closed
    method = Column(String(30))
    received_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    reporter_name = Column(String(60))
    reporter_phone = Column(String(40))
    product_group = Column(String(60))  # 공통코드 AS_PRODUCT_GROUP
    model_text = Column(String(200))
    item_id = Column(Integer)           # crm-rma: item.id 를 가리킨다 (여기서는 값만 그대로 둔다)
    part_no = Column(String(100))
    item_name = Column(String(200))
    qty = Column(Integer, nullable=False, default=1)
    symptom = Column(Text, nullable=False)
    site_name = Column(String(120))
    site_address = Column(String(255))
    site_phone = Column(String(40))
    action_note = Column(Text)          # 고객에게 안 보임
    delivery_note = Column(Text)        # 고객에게 안 보임
    replace_date = Column(String(10))
    manager_name = Column(String(60))
    rma_id = Column(Integer)            # crm-rma: rma.id 를 가리킨다 (여기서는 값만 그대로 둔다)
    rma_no_text = Column(String(60))
    replaced_parts = Column(Text)
    origin = Column(String(60))
    fault = Column(String(60))
    customer_notice = Column(Text)      # 고객에게 보이는 유일한 직원 입력 칸
    confirmed_at = Column(DateTime(timezone=True))
    confirmed_by_name = Column(String(60))
    closed_at = Column(DateTime(timezone=True))
    canceled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by_name = Column(String(60))
    updated_by_name = Column(String(60))

    company = relationship("Company")
    photos = relationship("AsCasePhoto", cascade="all, delete-orphan", order_by="AsCasePhoto.id")
    logs = relationship("AsCaseLog", cascade="all, delete-orphan", order_by="AsCaseLog.log_date, AsCaseLog.id")


class AsCasePhoto(Base):
    __tablename__ = "as_case_photo"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("as_case.id"), nullable=False, index=True)
    stored_name = Column(String(80), nullable=False)
    orig_name = Column(String(255))
    content_type = Column(String(100))
    size = Column(Integer)
    uploaded_by = Column(String(10), nullable=False)  # customer / staff
    uploaded_by_name = Column(String(60))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AsCaseLog(Base):
    """처리 기록. 고객에게는 내보내지 않는다 (고객 사이트는 취소할 때 한 줄을 더하기만 한다)."""
    __tablename__ = "as_case_log"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("as_case.id"), nullable=False, index=True)
    log_date = Column(String(10), nullable=False)
    kind = Column(String(20))
    content = Column(Text, nullable=False)
    issue_class = Column(String(10))
    issue_type = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by_name = Column(String(60))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by_name = Column(String(60))
