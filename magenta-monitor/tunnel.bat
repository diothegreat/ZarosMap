@echo off
REM Κρυπτογραφημένο SSH tunnel: VPS:127.0.0.1:%VPS_REMOTE_PORT% -> αυτό το PC:127.0.0.1:5000
REM Αν πέσει η σύνδεση, ξανασυνδέεται μόνο του κάθε 10 δευτερόλεπτα.
title Magenta Tunnel
cd /d "%~dp0"
if not exist "%~dp0vps_config.bat" (
    echo Λείπει το vps_config.bat - δες το VPS_SETUP.md
    pause
    exit /b 1
)
call "%~dp0vps_config.bat"
:loop
echo [%date% %time%] Σύνδεση στο %VPS_USER%@%VPS_HOST% ...
ssh -N -T -p %VPS_SSH_PORT% ^
    -o BatchMode=yes -o ExitOnForwardFailure=yes ^
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 ^
    -o StrictHostKeyChecking=accept-new ^
    -R 127.0.0.1:%VPS_REMOTE_PORT%:127.0.0.1:5000 ^
    %VPS_USER%@%VPS_HOST%
echo [%date% %time%] Το tunnel έπεσε - νέα προσπάθεια σε 10s
timeout /t 10 /nobreak >nul
goto loop
