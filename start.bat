@echo off
REM Launch GT-Code using the local virtual environment.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] No virtual environment found. Running setup first...
  call setup.bat
)

".venv\Scripts\python.exe" -m gt %*
