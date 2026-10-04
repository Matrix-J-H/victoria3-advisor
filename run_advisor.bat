@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m advisor %*
) else (
  python -m advisor %*
)
if "%~1"=="" pause
