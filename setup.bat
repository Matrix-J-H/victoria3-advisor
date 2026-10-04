@echo off
cd /d "%~dp0"
python -m venv .venv || goto :fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
echo.
echo Setup finished. Add your API key to .env, then use run_advisor.bat
pause
exit /b 0
:fail
echo Setup failed. See the messages above.
pause
exit /b 1
