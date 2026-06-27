@echo off
REM Double-click to register the AutoShortDaily Windows scheduled task.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_scheduler.ps1"
echo.
echo ----------------------------------------------------------
echo If you see "Registered." above, the daily task is set up.
echo Press any key to close this window.
pause >nul
