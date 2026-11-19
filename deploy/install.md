# 고객 A/S 사이트 — 서버 설치 안내 (Windows Server 2019 + IIS)

> ⚠ **이 안내는 실제 서버에서 한 번도 실행해 보지 못했다.** 프로그램 자체는 개발 PC 에서 테스트·화면 확인을 마쳤지만,
> IIS 설정·계정 권한·NAS 포트·공유 폴더는 이 서버에서 처음 해 보는 것이다. 막히는 곳이 나오면 그 화면이나 오류 글을 그대로 알려 주면 안내를 고친다.
> 값(계정·비밀번호·주소)은 `<자리표시>` 로만 적었다. 실제 값은 이 문서에 적어 두지 않는다.

## 0. 먼저 읽기

- 이 서버(KJC-MES)에는 **SQL Server 와 다른 웹사이트가 이미 돌고 있다.** 이 안내가 하는 일은 셋뿐이다:
  **IIS 모듈 1개 설치(HttpPlatformHandler) · 앱 풀 1개 추가 · 사이트 1개 추가.**
  서버 전체에 걸리는 변경은 "모듈 설치"와 6-2 의 "handlers 설정 잠금 풀기" 둘이다(둘 다 기존 사이트의 동작을 바꾸지 않는다).
  기존 사이트의 바인딩·인증서·앱 풀은 건드리지 않는다.
- 고객 사이트는 **이 서버의 SQL Server 를 쓰지 않는다.** NAS 의 PostgreSQL 만 쓴다. SQL Server 접속 정보를 이 프로그램 어디에도 넣지 않는다.
- 반드시 지킬 것 세 가지 (2절 · 9절에서 한다)
  1. 프로그램은 **관리자가 아닌** 전용 Windows 계정으로 돈다. 그 계정에 SQL Server 로그인·권한을 주지 않는다.
  2. 인터넷 쪽에서 이 서버로 열리는 포트는 **443 하나**다. SQL Server 포트(1433)가 밖에서 닫혀 있는지 확인한다.
  3. 그 계정이 쓸 수 있는 곳은 `Web-ASPortal\logs` 와 NAS 사진 폴더뿐이다.
- 자료(접수 내용·계정·사진)는 전부 NAS 에 있다. 이 서버에는 쌓이지 않는다(기록 파일만 생긴다).
- 이 설치는 지금의 CRM(NAS)을 바꾸지 않는다. 9절 전환 전까지 지금 주소의 고객 사이트는 그대로 살아 있다.

설치 폴더는 `C:\Webproject\Web-ASPortal` 로 적었다(이 서버의 다른 사이트들이 `C:\Webproject` 아래에 있다). 다른 곳에 두면 `web.config` 의 경로 세 곳을 같이 고친다.

---

## 1. NAS 준비

### 1-1. PostgreSQL 포트가 이 서버에서 닿는지

이 서버의 PowerShell 에서:

```powershell
Test-NetConnection <NAS 주소> -Port 15432
```

`TcpTestSucceeded : True` 면 1-3 으로. `False` 면 1-2.

### 1-2. 닿지 않을 때 (NAS 에서)

PostgreSQL 컨테이너(`kjc-postgres`)는 CRM 과 다른 도커 구성에 있다. 그 구성에서 아래 세 가지를 한다.
**하기 전에 그 구성의 설정 파일을 복사해 둔다(되돌리기용).**

1. 그 구성의 `docker-compose.yml` 에서 `kjc-postgres` 서비스에 포트를 낸다: `ports:` 에 `"15432:5432"` 한 줄(바깥 번호는 5432 가 아니라 **15432** — 시놀로지 NAS 가 자체 시스템용으로 5432 를 이미 쓰고 있어 같은 번호로는 컨테이너가 뜨지 않는다) → 그 구성을 다시 올린다.
2. `pg_hba.conf` 에 **이 서버와 전용 계정만** 허용하는 한 줄을 더하고 PostgreSQL 설정을 다시 읽힌다:
   ```text
   host    crm_rma    <PORTAL_DB_USER>    <이 서버 IP>/32    scram-sha-256
   ```
   - 도커가 포트를 내보내면 PostgreSQL 에는 출발지가 이 서버 IP 가 아니라 **도커 네트워크의 게이트웨이 주소**로 보일 수 있다.
     접속이 `no pg_hba.conf entry` 로 거절되면 그 오류 글에 적힌 주소를 이 줄에 쓴다(그때 출발지 제한은 3번의 방화벽이 맡는다).
   - 기존 `pg_hba.conf` 가 `md5` 를 쓰고 있으면 그 방식에 맞춘다.
3. DSM > 제어판 > 보안 > 방화벽: 포트 15432 를 **출발지 = 이 서버 IP 하나만** 허용하는 규칙을 더한다(그 밖의 출발지는 거부).

되돌리기: 방화벽 규칙 삭제 → `pg_hba.conf` 의 그 줄 삭제 → `ports` 줄 삭제 후 구성 다시 올리기.

### 1-3. 고객 사이트 전용 DB 계정

`db-account.sql` 을 열어 `<PORTAL_DB_USER>` · `<PORTAL_DB_PASSWORD>` 를 실제 값으로 바꾼 **사본**을 NAS 에 올리고(예: `/volume2/docker/crm-rma/db-account-run.sql`), DSM 작업 스케줄러(root)에서:

```bash
/usr/local/bin/docker exec -i kjc-postgres psql -U <DB 관리자 계정> -d crm_rma -v ON_ERROR_STOP=1 < /volume2/docker/crm-rma/db-account-run.sql > /volume2/docker/crm-rma/db-account.log 2>&1
```

로그에 `CREATE ROLE` 과 `GRANT` 줄들이 있고 `ERROR` 가 없어야 한다. **끝나면 값을 넣은 사본 파일을 NAS 에서 지운다.**
시퀀스 이름 오류가 나면 `psql` 에서 `\ds` 로 이름을 확인해 고친다. 계정을 없애는 방법은 `db-account.sql` 끝의 주석.

### 1-4. 사진 폴더

고객이 올린 사진은 NAS 의 `crm-rma/uploads/as` 폴더에 들어간다(CRM 이 지금 쓰는 바로 그 폴더).
이 서버에서 그 폴더가 공유 폴더로 보여야 한다: `\\<NAS 주소>\<공유 이름>\crm-rma\uploads\as`
`as` 폴더가 아직 없으면 NAS 에서 만든다(CRM 에서 사진을 한 번이라도 올렸으면 이미 있다).

---

## 2. 전용 Windows 계정

프로그램을 돌릴 계정을 하나 만든다. **관리자가 아닌** 일반 계정이어야 한다.

- 이 서버가 도메인에 가입돼 있지 않을 때(대부분 이 경우): 서버에 로컬 계정 `<서비스 계정>` 을 만들고,
  **NAS 에도 같은 ID · 같은 비밀번호의 사용자**를 만들어 사진 공유 폴더에 읽기/쓰기 권한을 준다.
- 도메인에 가입돼 있을 때: 도메인 계정을 하나 만들어 NAS 공유 폴더에 읽기/쓰기 권한을 준다.

```powershell
# 로컬 계정 만들기 (관리자 PowerShell). 비밀번호는 물어보는 창에 넣는다
New-LocalUser -Name "<서비스 계정>" -Password (Read-Host -AsSecureString "비밀번호") -PasswordNeverExpires -UserMayNotChangePassword
Add-LocalGroupMember -Group "IIS_IUSRS" -Member "<서비스 계정>"
```

- `Administrators` 그룹에 넣지 않는다.
- SQL Server 에 이 계정의 로그인을 만들지 않는다.

---

## 3. Python 과 프로그램 폴더

1. Python 은 서버에 이미 있는 **3.11** (`C:\Python311`)을 쓴다(2026-11-19 확인). 새로 설치하지 않는다. 3.10 이상이면 된다(3.7 은 안 된다).
2. 배포 묶음의 `Web-ASPortal` 폴더를 `C:\Webproject\Web-ASPortal` 로 복사한다. 안에 `backend` · `frontend` · `deploy` · `web.config` 가 있어야 한다.
   **`C:\Webproject\Web-ASPortal\web.config` 가 반드시 있어야 한다** — 묶음에는 이미 들어 있다. 없으면 `deploy\web.config` 를 그 자리로 복사한다.
   (이 파일이 없으면 IIS 가 프로그램을 띄우지 않고 폴더의 파일을 그대로 내준다.)
3. 관리자 PowerShell:

```powershell
cd C:\Webproject\Web-ASPortal\backend
C:\Python311\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force C:\Webproject\Web-ASPortal\logs
```

4. 폴더 권한 — 전용 계정은 프로그램 폴더를 읽기만 하고 `logs` 에만 쓴다:

```powershell
icacls C:\Webproject\Web-ASPortal /grant "<서비스 계정>:(OI)(CI)RX"
icacls C:\Webproject\Web-ASPortal\logs /grant "<서비스 계정>:(OI)(CI)M"
```

---

## 4. `backend\.env` 만들기

`backend\.env.example` 을 `backend\.env` 로 복사해 값을 채운다.

| 이름 | 넣을 값 |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://<PORTAL_DB_USER>:<PORTAL_DB_PASSWORD>@<NAS 주소>:15432/crm_rma` |
| `SECRET_KEY` | 새로 만든 긴 무작위 값. CRM 의 값과 같을 필요 없다. 아래 명령으로 만든다 |
| `AS_UPLOAD_DIR` | `\\<NAS 주소>\<공유 이름>\crm-rma\uploads\as` |
| `TRUSTED_PROXIES` | 그대로 둔다 |

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

`.env` 는 전용 계정과 관리자만 읽게 한다:

```powershell
icacls C:\Webproject\Web-ASPortal\backend\.env /inheritance:r /grant "Administrators:F" "<서비스 계정>:R"
```

비밀번호에 `@ : / # %` 같은 글자가 있으면 접속 주소 안에서 깨진다 — 그런 글자가 없는 비밀번호를 쓰거나 URL 인코딩한다.

---

## 5. 점검 (전용 계정으로)

전용 계정으로 PowerShell 을 연다(`runas /user:<서비스 계정> powershell`):

```powershell
cd C:\Webproject\Web-ASPortal\backend
.\.venv\Scripts\python.exe -m app.preflight
```
(위 줄이 `python -m app.preflight` 이다 — 새 버전을 올릴 때마다 다시 돌린다.)

다섯 줄(SECRET_KEY · DB 접속 · 표·칸 · DB 계정 권한 · 사진 폴더)이 모두 `[확인]` 이어야 한다.

| `[문제]` | 조치 |
|---|---|
| SECRET_KEY | `backend\.env` 에 `SECRET_KEY` 를 넣는다 |
| DB 접속 | 1-1 의 포트 확인 → `DATABASE_URL` 의 주소·계정·비밀번호 → 1-2 의 `pg_hba.conf` · 방화벽 |
| 표·칸 | 적힌 표·칸 이름을 본다. CRM(NAS)을 먼저 최신으로 올린다 |
| DB 계정 권한 | 적힌 표·시퀀스에 권한이 빠졌다 → 1-3 의 `db-account.sql` 을 다시 실행한다(시퀀스 이름이 다르면 `\ds` 로 확인) |
| 사진 폴더 | 전용 계정으로 `dir \\<NAS 주소>\<공유 이름>\crm-rma\uploads\as` 가 되는지, NAS 쪽 사용자·권한(2절) |

---

## 6. IIS

### 6-1. HttpPlatformHandler 설치

Microsoft 의 IIS 내려받기 페이지에서 **HttpPlatformHandler v1.2 (x64)** 설치 파일(msi)을 받아 설치한다.
IIS 관리자 > 서버 > 모듈에 `httpPlatformHandler` 가 보이면 된 것이다.

### 6-2. handlers 설정 잠금 풀기 (서버 전체 설정 · 한 번만)

사이트의 `web.config` 에서 처리기를 정할 수 있게 한다. 기존 사이트의 동작은 바뀌지 않는다.

```powershell
& $env:windir\system32\inetsrv\appcmd.exe unlock config -section:system.webServer/handlers
```
(이미 풀려 있으면 그대로다. 되돌리려면 같은 명령에서 `unlock` 을 `lock` 으로.)

### 6-3. 앱 풀과 사이트

```powershell
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"
& $appcmd add apppool /name:ASPortal /managedRuntimeVersion:"" /startMode:AlwaysRunning
& $appcmd set apppool ASPortal /processModel.idleTimeout:00:00:00 /processModel.maxProcesses:1 /recycling.periodicRestart.time:00:00:00
& $appcmd add site /name:ASPortal /bindings:"http/*:80:as.kjckiosk.co.kr" /physicalPath:"C:\Webproject\Web-ASPortal"
& $appcmd set app "ASPortal/" /applicationPool:ASPortal
```

앱 풀의 계정은 IIS 관리자에서 넣는다(비밀번호를 명령 기록에 남기지 않기 위해):
IIS 관리자 > 응용 프로그램 풀 > ASPortal > 고급 설정 > ID > 사용자 지정 계정 > `<서비스 계정>` 과 비밀번호.

- 바인딩의 호스트 이름(`as.kjckiosk.co.kr`)이 있어서 기존 사이트의 80·443 과 겹치지 않는다.
- `maxProcesses:1` — 프로세스는 반드시 1개(로그인 시도 제한이 프로세스 안 기억).

### 6-4. 떴는지 보기

프로그램은 사이트를 시작할 때가 아니라 **첫 요청이 올 때** 뜬다. 그래서 먼저 한 번 부른 뒤 기록을 본다
(주소가 아직 이 서버를 가리키지 않으므로 호스트 이름을 머리말로 넣어 부른다):

```powershell
& $appcmd start site ASPortal
Invoke-WebRequest http://127.0.0.1/api/portal/health -Headers @{ Host = "as.kjckiosk.co.kr" } -UseBasicParsing | Select-Object -Expand Content
Get-Content C:\Webproject\Web-ASPortal\logs\portal*.log -Tail 20
```

`{"status":"ok"}` 가 나오고, 기록에 `Uvicorn running on http://127.0.0.1:<포트>` 가 보여야 한다. 안 보이면 Windows 이벤트 뷰어 > 응용 프로그램에서 `HttpPlatformHandler` 오류를 본다
(대부분 `web.config` 의 경로가 틀렸거나 전용 계정이 폴더를 못 읽는 경우다).

---

## 7. 사내 확인 (전환 전)

이 서버에서 주소를 자기 자신으로 돌려 놓고 확인한다. 관리자 메모장으로 `C:\Windows\System32\drivers\etc\hosts` 에 한 줄:

```text
127.0.0.1    as.kjckiosk.co.kr
```

이 서버의 브라우저에서 `http://as.kjckiosk.co.kr/api/portal/health` → `{"status":"ok"}`.
그다음 `http://as.kjckiosk.co.kr/` 에서 실제 고객 계정(또는 CRM [고객 계정] 탭에서 만든 시험 계정)으로:

1. 로그인 (처음이면 비밀번호 변경)
2. A/S 접수 — 사진 1장 이상
3. **CRM(직원 화면) > C/S, A/S 관리에서 그 접수와 사진이 보이는지** ← 가장 중요
4. CRM 에서 그 건을 확인(품번 입력)하면 고객 화면의 상태가 '처리 중' 으로 바뀌는지
5. `http://as.kjckiosk.co.kr/docs` 와 `/api/as/cases` 가 404 인지

확인이 끝나면 `hosts` 에 넣은 줄을 지운다. 시험으로 만든 접수는 CRM 에서 정리한다.

---

## 8. 인증서와 443

1. `as.kjckiosk.co.kr` 인증서를 이 서버에 넣는다(IIS 관리자 > 서버 인증서).
2. IIS 관리자 > 사이트 > ASPortal > 바인딩 > 추가: 종류 `https`, 포트 `443`, 호스트 이름 `as.kjckiosk.co.kr`, **"서버 이름 표시 필요(SNI)" 체크**, 인증서 선택.
   SNI 를 쓰므로 기존 사이트의 443 인증서와 함께 쓸 수 있다. 기존 사이트의 바인딩은 건드리지 않는다.
3. https 로 7절의 1·2 를 한 번 더 본다.
4. https 가 되면 이 사이트의 **http(80) 바인딩을 지운다** — 로그인 정보가 암호화되지 않은 주소로 오가지 않게.

---

## 9. 전환

1. `as.kjckiosk.co.kr` 이 이 서버로 오게 한다: DNS 와 공유기/방화벽의 **443 전달**을 이 서버로.
2. DSM 리버스 프록시에 이 주소의 규칙이 있으면 없앤다(고객 요청이 NAS 를 거치지 않게).
3. **밖에서 열려 있는 포트를 확인한다** — 사내가 아닌 곳(휴대폰 데이터 등)에서:
   - `https://as.kjckiosk.co.kr/` 가 열린다
   - 이 서버의 공인 주소로 1433(SQL Server) · 15432 · 3389 가 **닫혀 있다** (포트 확인 사이트나 외부 PC 의 `Test-NetConnection <공인 주소> -Port 1433` → `False`)
4. 고객은 이날 한 번 다시 로그인하게 된다(서명 키가 바뀌므로). 미리 알릴 고객이 있으면 알린다.

---

## 10. 되돌리기

| 언제 | 방법 |
|---|---|
| 전환 전 | IIS 에서 ASPortal 사이트 중지. 아무 영향 없다 |
| 전환 후 | DNS·443 전달을 원래대로(NAS 의 지금 고객 사이트는 그대로 살아 있다) → ASPortal 사이트 중지 |
| 완전히 지우기 | 사이트·앱 풀 삭제 → `C:\Webproject\Web-ASPortal` 삭제 → 전용 계정 삭제 → `db-account.sql` 끝의 되돌리기 → 1-2 의 되돌리기 |

DB 구조는 어느 단계에서도 바뀌지 않는다. 새 서버로 받은 접수·사진은 NAS 에 있으므로 되돌려도 남는다.

---

## 11. 평소 관리

- 기록: `C:\Webproject\Web-ASPortal\logs\portal_*.log` (오래된 것은 가끔 지운다)
- 다시 시작: IIS 관리자 > 응용 프로그램 풀 > ASPortal > 재생
- 새 버전 올리기:
  1. 묶음의 `backend\app` · `frontend` · `backend\requirements.txt` 를 덮어쓴다 (`backend\.env` · `.venv` · `logs` 는 그대로)
  2. `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
  3. 앱 풀 재생
  4. `python -m app.preflight` (5절) → 모두 `[확인]`
- NAS 가 다시 켜지면: 프로그램은 다음 요청에서 DB 에 스스로 다시 붙는다. 사진 폴더가 안 보이면 사진 올리기만 "잠시 후 다시" 로 실패하고 접수 글은 된다.

## 12. HttpPlatformHandler 가 이 서버에서 맞지 않을 때

대안은 "프로그램을 Windows 서비스로 따로 띄우고(NSSM 등) IIS 는 ARR 로 넘기기"다.
이 방법은 **ARR 프록시 켜기가 서버 전체 설정**이라 기존 사이트에 영향이 갈 수 있어 처음부터 고르지 않았다. 이쪽으로 가야 하면 먼저 알려 주면 그 안내를 따로 만든다.
