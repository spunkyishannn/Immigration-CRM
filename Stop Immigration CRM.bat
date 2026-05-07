@echo off
setlocal
echo Stopping Immigration CRM (port 8765)...
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr ":8765" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)
taskkill /IM ImmigrationCRM.exe /F >nul 2>&1
echo Done.
timeout /t 2 >nul
endlocal
