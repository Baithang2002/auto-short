@echo off
REM ============================================================
REM  run_daily.bat - wrapper that the Windows scheduler calls.
REM  Runs pipeline_daily.py (generate + upload) and logs output.
REM  Safe to double-click for a manual run too.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "output" mkdir "output"
set "LOG=output\daily_run.log"

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo [run_daily] START  %date% %time% >> "%LOG%"

REM Prefer the py launcher; fall back to python on PATH.
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
echo [run_daily] interpreter: !PY! >> "%LOG%"

!PY! "pipeline_daily.py" >> "%LOG%" 2>&1
set "RC=!errorlevel!"

echo [run_daily] EXIT CODE: !RC! >> "%LOG%"
echo [run_daily] END    %date% %time% >> "%LOG%"
endlocal & exit /b %RC%
