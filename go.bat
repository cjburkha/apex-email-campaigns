@echo off
:: go.bat — Windows shortcut for send_outlook.py
:: Usage: go.bat <campaign> "<query>" [--dry-run] [--limit N]
:: Example: go.bat window-inspection "SELECT * FROM leads WHERE test_lead=1" --dry-run
cd /d "%~dp0"
call venv\Scripts\activate.bat
python send_outlook.py --campaign "%~1" --query "%~2" %3 %4 %5 %6 %7 %8 %9
