@echo off
setlocal
cd /d "%~dp0.."
if not exist "ClinicalTextDeid\.venv\Scripts\python.exe" (
  echo The existing ClinicalTextDeid Python environment is required.
  pause
  exit /b 1
)
"ClinicalTextDeid\.venv\Scripts\python.exe" -m ClinicalTextWeb.launch
if errorlevel 1 pause
