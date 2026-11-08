@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run_local_demo.py
) else if exist "..\ClinicalTextDeid\.venv\Scripts\python.exe" (
  "..\ClinicalTextDeid\.venv\Scripts\python.exe" run_local_demo.py
) else (
  py -3 run_local_demo.py
)
pause
