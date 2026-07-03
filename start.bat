@echo off
REM Launch GT-Code from its own venv, WITHOUT changing your working folder -
REM GT operates on the directory you run this from.
setlocal
set "GT_HOME=%~dp0"

if not exist "%GT_HOME%.venv\Scripts\gt.exe" (
  echo [!] GT-Code is not set up yet. Running setup first...
  pushd "%GT_HOME%"
  call setup.bat
  popd
)

"%GT_HOME%.venv\Scripts\gt.exe" %*
