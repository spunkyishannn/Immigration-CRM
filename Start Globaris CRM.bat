@echo off
setlocal
cd /d "%~dp0"

REM Dependencies (quiet)
python -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Pip install failed. Is Python on PATH? Try: python --version
  pause
  exit /b 1
)

REM Prefer pythonw = no black console window while CRM is open
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw "%~dp0desktop_app.py"
) else (
  REM Fallback: separate minimized console
  start "Globaris CRM" /MIN python "%~dp0desktop_app.py"
)

endlocal
