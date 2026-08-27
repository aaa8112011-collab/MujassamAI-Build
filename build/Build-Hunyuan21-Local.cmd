@echo off
setlocal
where pwsh.exe >nul 2>&1
if errorlevel 1 (
  echo PowerShell 7.4 or newer is required. Install it, then run this file again.
  pause
  exit /b 1
)
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-hunyuan21-local.ps1"
set "MJ_EXIT=%ERRORLEVEL%"
if not "%MJ_EXIT%"=="0" echo Build stopped with exit code %MJ_EXIT%.
pause
exit /b %MJ_EXIT%
