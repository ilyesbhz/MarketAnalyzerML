@echo off
REM Runs the daily pipeline with the project's own Python. Used by Windows Task Scheduler.
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m src.pipeline.run_daily
