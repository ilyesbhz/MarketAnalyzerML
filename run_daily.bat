@echo off
REM Runs the daily ML pipeline, then imports exported jobs into Next.js.
REM Used by Windows Task Scheduler.

cd /d "%~dp0"

"%~dp0venv\Scripts\python.exe" -m src.pipeline.run_daily
if errorlevel 1 exit /b 1

pushd "C:\Users\ilyesbhz\Desktop\9raya\web\job-analyzer"
call npm.cmd run import:jobs
if errorlevel 1 exit /b 1
popd

exit /b 0