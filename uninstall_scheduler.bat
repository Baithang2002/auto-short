@echo off
REM Double-click to remove the AutoShortDaily Windows scheduled task.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Unregister-ScheduledTask -TaskName 'AutoShortDaily' -Confirm:$false; Write-Host 'Removed AutoShortDaily.'"
echo.
echo Press any key to close this window.
pause >nul
