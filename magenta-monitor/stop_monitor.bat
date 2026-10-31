@echo off
REM Κλείνει Chrome + server + SSH tunnel του Magenta Monitor.
curl -s -o nul -X POST http://127.0.0.1:5000/api/shutdown
taskkill /fi "WINDOWTITLE eq Magenta Tunnel*" /t /f >nul 2>&1
exit /b 0
