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
set "LOG_FILE=%LOG_DIR%\register-ozon-runner-%STAMP%.log"
echo Logging to %LOG_FILE%
echo Copy the GitHub runner config command before running this wrapper.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& { & '%~dp0register-ozon-runner.ps1' -TokenFromClipboard 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append }"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Exit code: %EXIT_CODE%
echo Log: %LOG_FILE%
pause
exit /b %EXIT_CODE%
