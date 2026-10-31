@echo off
REM Εφάπαξ εγκατάσταση: virtualenv + βιβλιοθήκες.
cd /d "%~dp0"
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env"
if not exist "config.json" copy "config.example.json" "config.json"
echo.
echo Έτοιμο. Άνοιξε το .env και βάλε τα στοιχεία σου, και το config.json για τα κανάλια.
pause
