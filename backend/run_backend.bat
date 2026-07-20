@echo off
rem uvicorn with auto-restart: if the backend crashes, relaunch after 2 s.
rem faulthandler prints native-crash thread dumps into backend.log.
rem Python 3.13 venv is mandatory: 3.14 + polars on Windows produced native
rem access violations (see locks.py). The concurrent-read gate is chosen by
rem locks.py from the RUNNING Python version (4 on 3.13, 1 on 3.14+win32); do
rem NOT set KIHA_MAX_CONCURRENT_READS here — a hardcoded 4 would force a 3.14
rem venv straight past the very gate that exists to stop those crashes (1.4).
cd /d %~dp0
set PYTHONFAULTHANDLER=1
:loop
echo [%date% %time%] starting uvicorn >> backend.log
.venv\Scripts\python.exe run.py >> backend.log 2>&1
echo [%date% %time%] uvicorn exited (code %errorlevel%), restarting in 2 s >> backend.log
timeout /t 2 /nobreak >nul
goto loop
