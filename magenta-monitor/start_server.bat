@echo off
REM Ξεκινά τον Flask server του Magenta Monitor.
REM Το παράθυρο μένει ανοιχτό (ελαχιστοποιημένο) ώστε να φαίνονται τα μηνύματα/σφάλματα.
cd /d "%~dp0"
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
start "Magenta Monitor Server" /min cmd /k ""%PY%" "%~dp0app.py""
