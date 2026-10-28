@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_personal_app.ps1"
set "STOCKLENS_EXIT=%ERRORLEVEL%"
if not "%STOCKLENS_EXIT%"=="0" (
  echo.
  echo StockLens could not start. Review the message above.
  pause
)
exit /b %STOCKLENS_EXIT%
