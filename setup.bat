@echo off
REM ============================================================
REM  GT-Code one-time setup for Windows
REM  Creates a local virtual environment and installs deps.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === GT-Code setup ===
echo.

REM --- find Python ---
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not on your PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo and CHECK "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

echo Creating virtual environment in .venv ...
python -m venv .venv
if errorlevel 1 (
  echo [ERROR] Could not create the virtual environment.
  pause
  exit /b 1
)

echo Installing dependencies ...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check your internet connection.
  pause
  exit /b 1
)

echo.
echo === Setup complete! ===
echo Next:
echo   1) Make sure Ollama is running and LM Studio's server is started.
echo   2) Double-click start.bat  (or run it from a terminal).
echo   3) In GT, type /models to confirm your model ids, then edit config.yaml.
echo.
pause
