@echo off
REM Ξεκινά τον Flask server του Magenta Monitor (αν δεν τρέχει ήδη).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)
start "Magenta Monitor Server" /min %PY% app.py
