@echo off
rem stop whatever listens on ports 8000 (backend) and 3000 (frontend dev server)
rem kill the auto-restart wrapper first so it cannot relaunch uvicorn
taskkill /f /fi "WINDOWTITLE eq ptt-backend*" >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :3000 ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
echo stopped.
