@echo off
REM ============================================================
REM  GT-Code doctor - diagnoses a broken install, step by step.
REM  Run this from the GT-code folder and read the output top to
REM  bottom: the FIRST [FAIL] is where your install breaks.
REM  Send the full output when reporting a problem.
REM ============================================================
setlocal
cd /d "%~dp0"
set "FAILED="

echo.
echo === GT-Code doctor ===
echo Folder   : %CD%
echo Windows  : %OS%
echo User     : %USERNAME%
echo.

REM ---- 1. Python ------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo [FAIL] 1. Python is not on PATH.
  echo         FIX: install Python 3.10+ from python.org and tick
  echo         "Add python.exe to PATH", then reopen this terminal
  echo         and re-run setup.bat
  set "FAILED=1"
) else (
  for /f "tokens=*" %%V in ('python --version 2^>^&1') do echo [ OK ] 1. Python found: %%V
)

REM ---- 2. venv exists -------------------------------------------
if exist ".venv\Scripts\python.exe" (
  echo [ OK ] 2. Virtual environment exists: .venv
) else (
  echo [FAIL] 2. No .venv folder here - setup never completed.
  echo         FIX: run setup.bat and watch for errors.
  set "FAILED=1"
)

REM ---- 3. GT installed inside the venv ---------------------------
if exist ".venv\Scripts\gt.exe" (
  echo [ OK ] 3. GT is installed in the venv: .venv\Scripts\gt.exe
) else (
  echo [FAIL] 3. .venv\Scripts\gt.exe is missing - the pip install step failed.
  echo         FIX: re-run setup.bat. If it fails again, run this and
  echo         send the output:  .venv\Scripts\python.exe -m pip install -e .
  set "FAILED=1"
)

REM ---- 4. GT actually starts --------------------------------------
".venv\Scripts\gt.exe" --version >nul 2>nul
if errorlevel 1 (
  echo [FAIL] 4. gt.exe exists but does not run.
  echo         FIX: run this and send the output:
  echo         .venv\Scripts\gt.exe --version
  set "FAILED=1"
) else (
  for /f "tokens=*" %%V in ('".venv\Scripts\gt.exe" --version 2^>^&1') do echo [ OK ] 4. GT runs: %%V
)

REM ---- 5. global shim exists --------------------------------------
if exist "%USERPROFILE%\.gt\bin\gt.cmd" (
  echo [ OK ] 5. Global command exists: %USERPROFILE%\.gt\bin\gt.cmd
) else (
  echo [FAIL] 5. %USERPROFILE%\.gt\bin\gt.cmd is missing.
  echo         FIX: re-run setup.bat - it creates this and puts it on PATH.
  set "FAILED=1"
)

REM ---- 6. shim folder is on the user PATH -------------------------
powershell -NoProfile -Command "$bin=$env:USERPROFILE+'\.gt\bin'; $p=[Environment]::GetEnvironmentVariable('Path','User'); if(($p -split ';') -contains $bin){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 (
  echo [FAIL] 6. %USERPROFILE%\.gt\bin is NOT on your user PATH.
  echo         FIX: re-run setup.bat, or add it by hand:
  echo         Start menu, type "environment variables", edit the
  echo         USER "Path" variable, add:  %USERPROFILE%\.gt\bin
  echo         Then open a NEW terminal.
  set "FAILED=1"
) else (
  echo [ OK ] 6. %USERPROFILE%\.gt\bin is on your user PATH.
)

REM ---- 7. 'gt' resolves in THIS terminal ---------------------------
where gt >nul 2>nul
if errorlevel 1 (
  echo [WARN] 7. 'gt' does not resolve in THIS terminal.
  echo         If checks 5 and 6 are OK, this terminal is just older than
  echo         the PATH change: open a NEW terminal and try again.
) else (
  for /f "tokens=*" %%P in ('where gt 2^>nul') do echo [ OK ] 7. 'gt' resolves to: %%P
)

REM ---- 8. Ollama installed -----------------------------------------
where ollama >nul 2>nul
if errorlevel 1 (
  echo [FAIL] 8. Ollama is not installed or not on PATH.
  echo         FIX: winget install -e --id Ollama.Ollama
  echo         then reopen the terminal.
  set "FAILED=1"
) else (
  echo [ OK ] 8. Ollama is installed.
)

REM ---- 9. Ollama server responding ---------------------------------
".venv\Scripts\python.exe" -c "import requests;requests.get('http://localhost:11434/v1/models',timeout=4)" >nul 2>nul
if errorlevel 1 (
  echo [FAIL] 9. The Ollama server is not responding on localhost:11434.
  echo         FIX: start Ollama from the Start menu, wait 10 seconds,
  echo         then run doctor.bat again.
  set "FAILED=1"
) else (
  echo [ OK ] 9. Ollama server is responding.
)

REM ---- 10. models present -------------------------------------------
".venv\Scripts\python.exe" -c "import requests,sys;d=(requests.get('http://localhost:11434/v1/models',timeout=4).json().get('data') or []);sys.exit(0 if d else 1)" >nul 2>nul
if errorlevel 1 (
  echo [WARN] 10. Ollama serves no models yet.
  echo          FIX: type gt and let the first-launch wizard download them,
  echo          or manually:  ollama pull llama3.2:3b
) else (
  echo [ OK ] 10. Ollama has models available.
)

echo.
if defined FAILED (
  echo === RESULT: problems found - fix the FIRST [FAIL] above, then re-run doctor.bat ===
  echo     Full guide: TROUBLESHOOTING.md in this folder
) else (
  echo === RESULT: everything checks out. Open a NEW terminal and type: gt ===
)
echo.
pause
