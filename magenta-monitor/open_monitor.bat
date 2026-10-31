@echo off
REM Ανοίγει το Magenta Monitor: ξεκινά τον server ΜΟΝΟ αν δεν τρέχει ήδη,
REM και μετά ανοίγει τη σελίδα στον browser. Το καλεί το Node-RED.
cd /d "%~dp0"
curl -s -o nul http://127.0.0.1:5000/api/health
if %errorlevel%==0 goto open
call "%~dp0start_server.bat"
REM περίμενε μέχρι να απαντήσει ο server (έως ~20 δευτερόλεπτα)
for /l %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    curl -s -o nul http://127.0.0.1:5000/api/health && goto open
)
:open
start "" http://127.0.0.1:5000
