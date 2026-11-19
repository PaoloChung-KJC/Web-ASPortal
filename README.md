# Web-ASPortal — KJC 고객 A/S 사이트

고객이 로그인해 A/S 를 접수하고(내용 + 사진) 처리 상황을 보는 사이트(as.kjckiosk.co.kr)다.
직원용 CRM(`crm-rma`, NAS)에서 고객이 쓰는 부분만 떼어 낸 독립 프로그램이며 Windows Server + IIS 에서 돈다.

- **자료는 전부 NAS 에 있다.** 접수·계정은 NAS PostgreSQL(`crm_rma`), 사진은 NAS 의 `crm-rma/uploads/as`. 이 프로그램이 도는 서버에는 쌓이지 않는다.
- **이 프로그램은 DB 표를 만들거나 고치지 않는다.** 표 구조의 주인은 `crm-rma` 다.
- 직원 화면(C/S·A/S 관리 · 고객계정 발급 · 현황)은 `crm-rma` 에 있다. 고객 계정도 거기서 만든다.

## 폴더

```text
backend/app/            프로그램 (FastAPI)
  main.py               앱: /api/portal/ 과 화면 파일만 내보낸다. 문서 주소는 끔
  routers/portal.py     고객 API
  services/             로그인 · 고객용 규칙 · 사진 · 접수번호 채번 · 요청자 IP
  models.py             고객 사이트가 쓰는 표 9개 (crm-rma 와 같은 칸)
  preflight.py          설치 점검 (python -m app.preflight)
backend/tests/          테스트 (메모리 SQLite — 운영 DB 에 닿지 않는다)
backend/tools/          dev_seed.py(로컬 시험 자료) · dump_columns.py(표 정의 대조용)
frontend/               화면 파일 3개 (index.html · portal.jsx · portal.css)
deploy/                 web.config(IIS) · db-account.sql(전용 DB 계정) · install.md(서버 설치 안내) · run-local.bat
```

## 이 PC 에서 띄워 보기

```powershell
deploy\run-local.bat
```
`http://localhost:8030/` — SQLite(`backend/dev.db`)에 시험 거래처·계정을 만들어 띄운다. 시험 계정은 `backend/tools/dev_seed.py` 맨 위에 있다.

## 테스트

```powershell
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

가상환경은 Python 3.12 로 만든다(`requirements.txt` 의 고정 버전이 3.12 기준).

## 서버 설치

`deploy/install.md` 를 위에서 아래로 따라 한다.

## crm-rma 에서 A/S 표를 바꿨을 때 ⚠

`as_case` · `as_case_photo` · `as_case_log` · `as_portal_account` · `record_revision` · `auto_number_rule` · `common_code(_group)` 의
칸을 `crm-rma` 에서 더하거나 바꾸면 **여기 `backend/app/models.py` 도 같이 고친다.**
`tests/test_crm_schema_match.py` 가 두 프로그램의 표 정의를 대조해 어긋난 칸을 알려 준다(`crm-rma` 폴더가 옆에 있을 때).
서버에서는 `python -m app.preflight` 가 실제 DB 와 맞는지 본다.

고객용 규칙(상태 이름, 고객이 고칠 수 있는 때, 고객에게 보이는 칸)은 `backend/app/services/case_rules.py` 에 있다.
`crm-rma/backend/app/services/as_case.py` 의 같은 규칙을 바꾸면 여기도 같이 바꾼다.
