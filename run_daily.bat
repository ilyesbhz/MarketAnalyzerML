@echo off
REM Daily pipeline, used by Windows Task Scheduler. The web app import is its last step (import_app).
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m src.pipeline.run_daily
exit /b %errorlevel%
