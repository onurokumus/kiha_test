@echo off
rem start backend + frontend as minimized background windows
start "ptt-backend" /min cmd /c "%~dp0backend\run_backend.bat"
start "ptt-frontend" /min cmd /c "cd /d %~dp0frontend && npm run dev"
echo servers starting...
echo   backend:  http://127.0.0.1:8000
echo   frontend: http://localhost:3000
