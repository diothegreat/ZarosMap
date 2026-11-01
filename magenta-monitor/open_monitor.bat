@echo off
REM Χρήση:  open_monitor.bat local   -> ανοίγει http://127.0.0.1:5000
REM         open_monitor.bat vps     -> + SSH tunnel και ανοίγει τη διεύθυνση του VPS
REM Ξεκινά τον server ΜΟΝΟ αν δεν τρέχει ήδη. Το καλεί το Node-RED.
cd /d "%~dp0"
set MODE=%~1
if "%MODE%"=="" set MODE=local

curl -s -o nul http://127.0.0.1:5000/api/health
if %errorlevel%==0 goto server_ok
call "%~dp0start_server.bat"
for /l %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    curl -s -o nul http://127.0.0.1:5000/api/health && goto server_ok
)
:server_ok

if /i "%MODE%"=="vps" goto vps
start "" http://127.0.0.1:5000
exit /b 0

:vps
if not exist "%~dp0vps_config.bat" (
    echo Λείπει το vps_config.bat - δες το VPS_SETUP.md
    pause
    exit /b 1
)
REM Διαβάζει ΜΟΝΟ τις γραμμές "set ..." του vps_config.bat (αγνοεί ό,τι άλλο)
for /f "usebackq delims=" %%L in (`findstr /r /b /i /c:"[ ]*set [A-Za-z_]*=" "%~dp0vps_config.bat"`) do %%L
tasklist /v /fi "WINDOWTITLE eq Magenta Tunnel*" | find /i "Magenta Tunnel" >nul
if errorlevel 1 start "Magenta Tunnel" /min cmd /c "%~dp0tunnel.bat"
timeout /t 4 /nobreak >nul
start "" %VPS_URL%
exit /b 0
