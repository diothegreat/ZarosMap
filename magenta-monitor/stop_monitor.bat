@echo off
REM Κλείνει Chrome + server του Magenta Monitor (αν τρέχει).
curl -s -o nul -X POST http://127.0.0.1:5000/api/shutdown
exit /b 0
