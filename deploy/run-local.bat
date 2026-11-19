@echo off
rem 이 PC 에서 고객 A/S 사이트를 띄워 본다 (SQLite, http://localhost:8030/)
cd /d %~dp0..\backend
.venv\Scripts\python.exe tools\dev_seed.py || exit /b 1
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8030 --no-proxy-headers
