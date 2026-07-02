@echo off
REM ============================================================
REM  GT-Code one-shot setup for Windows. Safe to re-run any time.
REM    1. venv + Python dependencies
REM    2. Ollama (auto-installed via winget if missing)
REM    3. the local models GT needs (~8 GB the first time)
REM    4. a global "gt" command so you can run GT from any folder
REM  LM Studio is OPTIONAL - GT falls back to Ollama without it.
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

REM --- venv + deps ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing Python dependencies ...
call ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
call ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check your internet connection.
  pause
  exit /b 1
)

REM --- Ollama: install automatically if missing ---
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama not found - installing it with winget ...
  winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo [ERROR] Could not install Ollama automatically.
    echo Download it from https://ollama.com/download then re-run setup.bat
    pause
    exit /b 1
  )
  REM make it visible to THIS window without reopening the terminal
  set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
)

REM --- make sure the Ollama server is running ---
ollama list >nul 2>nul
if errorlevel 1 (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    echo Starting Ollama ...
    start "" /min "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    timeout /t 8 /nobreak >nul
  )
)

REM --- pull the models GT needs (skips anything already downloaded) ---
echo.
echo Downloading local models - the slow part, ~8 GB on a fresh machine:
echo   qwen3:8b (coder) + llama3.2:3b (router) + nomic-embed-text (memory)
echo.
for %%M in (qwen3:8b llama3.2:3b nomic-embed-text) do (
  ollama pull %%M
  if errorlevel 1 echo [WARN] could not pull %%M - run "ollama pull %%M" later.
)

REM --- put a global "gt" command on PATH ---
REM WindowsApps is user-writable and already on PATH on Windows 10/11.
set "SHIM=%LOCALAPPDATA%\Microsoft\WindowsApps\gt.cmd"
(
  echo @echo off
  echo "%CD%\.venv\Scripts\python.exe" -m gt %%*
) > "%SHIM%" 2>nul
if exist "%SHIM%" (
  echo Installed the "gt" command.
) else (
  echo [WARN] Couldn't create %SHIM% - use start.bat instead.
)

REM --- LM Studio is optional: bigger brain when present ---
echo.
".venv\Scripts\python.exe" -c "import requests;requests.get('http://localhost:1234/v1/models',timeout=3)" >nul 2>nul
if errorlevel 1 (
  echo [i] LM Studio not detected - that's fine: GT runs everything on Ollama.
  echo     For a bigger "brain", install LM Studio, load your 28B model, and
  echo     start its server ^(Developer tab^). GT picks it up on next launch.
) else (
  echo [i] LM Studio detected on :1234 - GT will use it for the "brain" role.
)

echo.
echo === Setup complete! ===
echo Open a NEW terminal, cd into any project, and type:  gt
echo ^(or double-click start.bat^)
pause
