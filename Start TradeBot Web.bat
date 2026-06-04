@echo off
setlocal

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8007"
set "URL=http://%HOST%:%PORT%"

echo TradeBot launcher
echo Project: %CD%
echo URL: %URL%
echo.

call :check_server
if %ERRORLEVEL%==0 (
    echo TradeBot is already running. Opening browser...
    start "" "%URL%"
    exit /b 0
)

echo TradeBot is not running. Starting server in a separate window...
start "TradeBot Web Server" /D "%~dp0" cmd /k python -B main.py --host %HOST% --port %PORT%

echo Waiting for TradeBot to become ready...
for /L %%I in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    call :check_server
    if not errorlevel 1 goto ready
)

echo.
echo TradeBot did not become ready within 20 seconds.
echo Check the "TradeBot Web Server" window for the Python error message.
pause
exit /b 1

:ready
echo TradeBot is ready. Opening browser...
start "" "%URL%"
exit /b 0

:check_server
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing '%URL%' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } else { exit 1 } } catch { exit 1 }"
exit /b %ERRORLEVEL%
