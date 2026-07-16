@echo off
rem uvicorn with auto-restart: if the backend crashes, relaunch after 2 s.
rem faulthandler prints native-crash thread dumps into backend.log.
rem Python 3.13 venv is mandatory: 3.14 + polars on Windows produced native
rem access violations (see locks.py); the venv pin keeps the read gate at 4.
cd /d %~dp0
set PYTHONFAULTHANDLER=1
set KIHA_MAX_CONCURRENT_READS=4
:loop
echo [%date% %time%] starting uvicorn >> backend.log
.venv\Scripts\python.exe run.py >> backend.log 2>&1
echo [%date% %time%] uvicorn exited (code %errorlevel%), restarting in 2 s >> backend.log
timeout /t 2 /nobreak >nul
goto loop
