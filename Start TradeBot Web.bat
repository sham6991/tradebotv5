@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(main.py|web_app.py)' -and $_.CommandLine -match '--port 800[0-9]' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
start "" http://127.0.0.1:8006
python main.py --host 127.0.0.1 --port 8006
pause
