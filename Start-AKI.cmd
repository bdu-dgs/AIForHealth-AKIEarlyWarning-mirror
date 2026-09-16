@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup-AKI.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m dashboard.launcher %*
set "startExit=%ERRORLEVEL%"
if not "%startExit%"=="0" pause
exit /b %startExit%
