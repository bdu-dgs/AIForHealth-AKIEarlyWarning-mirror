@echo off
rem Demo playback: synthetic patients, a stepped clock (+1 h button) and the live model, in a fresh data folder.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup-AKI.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m dashboard.launcher --demo %*
set "startExit=%ERRORLEVEL%"
if not "%startExit%"=="0" pause
exit /b %startExit%
