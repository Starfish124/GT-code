@echo off
REM ============================================================
REM  GT-Code one-shot setup for Windows. Safe to re-run any time.
REM    1. venv + Python dependencies
REM    2. Ollama (auto-installed via winget if missing)
REM    3. the baseline local models (GT's first launch offers the bigger ones)
REM    4. a global "gt" command so you can run GT from any folder
REM  LM Studio is OPTIONAL - GT falls back to Ollama without it.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === GT-Code setup ===
echo.

REM --- find a REAL Python: 3.10+, FULL install with venv support ---
REM Rejects the Microsoft Store stub and the "embeddable" zip package
REM (python-3.x-embed-amd64) - those have no venv/pip and cannot run GT.
set "PYCHECK=import sys,venv;raise SystemExit(0 if sys.version_info>=(3,10) else 1)"
set "PYCMD=python"
python -c "%PYCHECK%" >nul 2>nul
if not errorlevel 1 goto :python_ok
set "PYCMD=py -3"
py -3 -c "%PYCHECK%" >nul 2>nul
if not errorlevel 1 goto :python_ok

echo [ERROR] No usable Python found. GT needs a FULL Python 3.10+ install.
echo.
echo What this PC finds when you type "python":
where python 2>nul
echo.
echo Common causes:
echo   - the "embeddable" zip package - a folder like python-3.x-embed-amd64.
echo     It has NO venv and NO pip and can never run GT. Remove it from PATH.
echo   - the Microsoft Store stub that opens the Store instead of Python.
echo   - a Python older than 3.10.
echo.
echo FIX: install the real thing, REOPEN this terminal, re-run setup.bat:
echo   winget install -e --id Python.Python.3.12
echo   or python.org/downloads - tick "Add python.exe to PATH"
pause
exit /b 1

:python_ok
for /f "tokens=*" %%V in ('%PYCMD% --version 2^>^&1') do echo Using %%V


REM --- venv + deps ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  %PYCMD% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing GT-Code into its own environment ...
call ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
REM Editable install: puts the 'gt' package AND a real 'gt' command inside
REM the venv, so GT launches from ANY folder (no more "No module named gt").
call ".venv\Scripts\python.exe" -m pip install --quiet -e .
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

REM --- pull the baseline models (skips anything already downloaded) ---
REM GT's first launch evaluates this PC's hardware and offers the bigger
REM models (up to 14B) only if the machine can actually run them.
echo.
echo Downloading the baseline models (~2.3 GB on a fresh machine):
echo   llama3.2:3b (minimum) + nomic-embed-text (memory)
echo.
for %%M in (llama3.2:3b nomic-embed-text) do (
  ollama pull %%M
  if errorlevel 1 echo [WARN] could not pull %%M - run "ollama pull %%M" later.
)

REM --- put a global "gt" command on PATH ---
REM The shim calls the venv's own gt.exe: GT always runs from its OWN venv
REM here in the GT-code folder, and operates on whatever folder you're in.
REM
REM We install the shim into %USERPROFILE%\.gt\bin and add THAT to the user
REM PATH ourselves. (Relying on the WindowsApps folder breaks on managed /
REM corporate laptops where it isn't on PATH - the classic
REM "'gt' is not recognized" error.)
set "GTBIN=%USERPROFILE%\.gt\bin"
if not exist "%GTBIN%" mkdir "%GTBIN%" >nul 2>nul
(
  echo @echo off
  echo "%~dp0.venv\Scripts\gt.exe" %%*
) > "%GTBIN%\gt.cmd"
if not exist "%GTBIN%\gt.cmd" (
  echo [WARN] Couldn't create %GTBIN%\gt.cmd - use start.bat instead.
)

REM Also drop the old WindowsApps shim; harmless where it works, and it
REM keeps existing installs consistent. Failure here is fine.
(
  echo @echo off
  echo "%~dp0.venv\Scripts\gt.exe" %%*
) > "%LOCALAPPDATA%\Microsoft\WindowsApps\gt.cmd" 2>nul

REM Add %USERPROFILE%\.gt\bin to the USER Path (persistent + idempotent).
powershell -NoProfile -Command "$bin=$env:USERPROFILE+'\.gt\bin'; $p=[Environment]::GetEnvironmentVariable('Path','User'); if([string]::IsNullOrEmpty($p)){ [Environment]::SetEnvironmentVariable('Path',$bin,'User'); 'Added '+$bin+' to your PATH.' } elseif(($p -split ';') -notcontains $bin){ [Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$bin),'User'); 'Added '+$bin+' to your PATH.' } else { 'PATH already set up.' }"
if errorlevel 1 (
  echo [WARN] Could not update your PATH automatically. Add this folder to
  echo        your user PATH by hand:  %GTBIN%
)

REM Make it work in THIS window too (new terminals pick up the user PATH).
set "PATH=%GTBIN%;%PATH%"

REM --- sanity check: 'gt' must resolve via PATH from a DIFFERENT directory ---
pushd "%TEMP%"
call gt --version >nul 2>nul
if errorlevel 1 (
  echo [WARN] "gt" self-test failed.
  echo        Try a NEW terminal first. If it still fails, run doctor.bat
  echo        for a step-by-step diagnosis, or launch GT directly with:
  echo          %~dp0start.bat
) else (
  echo Self-test OK: "gt" works from any folder.
)
popd

REM --- LM Studio is optional: bigger brain when present ---
echo.
".venv\Scripts\python.exe" -c "import requests;requests.get('http://localhost:1234/v1/models',timeout=3)" >nul 2>nul
if errorlevel 1 (
  echo [i] LM Studio not detected - that's fine: GT runs everything on Ollama.
  echo     GT's first launch evaluates this PC and downloads the best models
  echo     for it ^(3B minimum, 14B maximum - bigger is too slow to be useful^).
) else (
  echo [i] LM Studio detected on :1234 - GT will use it for the "brain" role.
)

echo.
echo === Setup complete! ===
echo Open a NEW terminal, cd into any project, and type:  gt
echo ^(or double-click start.bat^)
pause
