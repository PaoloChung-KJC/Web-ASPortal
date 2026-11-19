-- 고객 A/S 사이트 전용 DB 계정 (NAS PostgreSQL, DB crm_rma)
--
-- 이 계정은 고객 사이트가 쓰는 표에만, 필요한 동작만 할 수 있다. 영업·품질·MES·사용자 표에는 권한이 없다.
-- 실행 전에 <PORTAL_DB_USER> · <PORTAL_DB_PASSWORD> 두 자리를 실제 값으로 바꾼다 (이 파일에 값을 저장해 두지 않는다).
-- 실행은 NAS 에서 DB 관리자 계정으로 (설치 안내 1절).
--
-- 시퀀스 이름은 PostgreSQL 기본 규칙(<표>_id_seq)으로 적었다. 다르면 psql 에서 \ds 로 확인해 고친다.

CREATE ROLE <PORTAL_DB_USER> LOGIN PASSWORD '<PORTAL_DB_PASSWORD>';

GRANT CONNECT ON DATABASE crm_rma TO <PORTAL_DB_USER>;
GRANT USAGE ON SCHEMA public TO <PORTAL_DB_USER>;

-- 읽기만. 거래처 표는 id · name 두 칸만 (사업자번호·연락처 등 다른 칸은 읽지 못한다)
GRANT SELECT (id, name) ON company TO <PORTAL_DB_USER>;
GRANT SELECT ON common_code TO <PORTAL_DB_USER>;
GRANT SELECT ON common_code_group TO <PORTAL_DB_USER>;

-- 고객 계정: 로그인 기록·비밀번호 변경 (계정을 만들거나 지우는 것은 CRM 직원 화면)
GRANT SELECT, UPDATE ON as_portal_account TO <PORTAL_DB_USER>;

-- 접수 건: 등록·수정 (지우지 않는다)
GRANT SELECT, INSERT, UPDATE ON as_case TO <PORTAL_DB_USER>;

-- 사진: 올리기·지우기
GRANT SELECT, INSERT, DELETE ON as_case_photo TO <PORTAL_DB_USER>;

-- 처리 기록: 고객이 취소할 때 한 줄 더하기
GRANT SELECT, INSERT ON as_case_log TO <PORTAL_DB_USER>;

-- 수정 번호: 직원 화면과 동시 저장 확인을 맞춘다
GRANT SELECT, INSERT, UPDATE ON record_revision TO <PORTAL_DB_USER>;

-- 접수번호 채번 규칙: 읽기 (규칙이 아직 없을 때 기본 규칙 한 줄 만들기)
GRANT SELECT, INSERT ON auto_number_rule TO <PORTAL_DB_USER>;

-- 새 줄 번호(시퀀스)
GRANT USAGE, SELECT ON SEQUENCE as_case_id_seq TO <PORTAL_DB_USER>;
GRANT USAGE, SELECT ON SEQUENCE as_case_photo_id_seq TO <PORTAL_DB_USER>;
GRANT USAGE, SELECT ON SEQUENCE as_case_log_id_seq TO <PORTAL_DB_USER>;
GRANT USAGE, SELECT ON SEQUENCE auto_number_rule_id_seq TO <PORTAL_DB_USER>;

-- ───────────── 되돌리기 (계정 없애기) ─────────────
-- REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM <PORTAL_DB_USER>;
-- REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM <PORTAL_DB_USER>;
-- REVOKE USAGE ON SCHEMA public FROM <PORTAL_DB_USER>;
-- REVOKE CONNECT ON DATABASE crm_rma FROM <PORTAL_DB_USER>;
-- DROP ROLE <PORTAL_DB_USER>;
