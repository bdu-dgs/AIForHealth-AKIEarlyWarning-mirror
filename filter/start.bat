@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing virtual environment. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
pause
