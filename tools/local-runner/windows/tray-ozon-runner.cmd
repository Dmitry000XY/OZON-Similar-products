@echo off
setlocal
cd /d "%~dp0"
set "LOG_DIR=D:\ozon-local-runner\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "STAMP=%DATE%_%TIME%"
set "STAMP=%STAMP:/=-%"
set "STAMP=%STAMP::=-%"
set "STAMP=%STAMP:.=-%"
set "STAMP=%STAMP:,=-%"
set "STAMP=%STAMP: =_%"
set "LOG_FILE=%LOG_DIR%\tray-ozon-runner-%STAMP%.log"
echo Starting tray monitor. Logging to %LOG_FILE%
powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -Command "& { & '%~dp0tray-ozon-runner.ps1' 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append }"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Tray monitor exited with code: %EXIT_CODE%
echo Log: %LOG_FILE%
pause
exit /b %EXIT_CODE%
