@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-windows.ps1" %*
set "setupExit=%ERRORLEVEL%"
if not "%setupExit%"=="0" pause
exit /b %setupExit%
