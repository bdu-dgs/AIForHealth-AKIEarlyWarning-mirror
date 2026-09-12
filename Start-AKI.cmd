@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup-AKI.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m dashboard.launcher %*
if errorlevel 1 pause
