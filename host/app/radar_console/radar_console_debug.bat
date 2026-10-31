@echo off
rem Radar console launcher with console window (for debugging tracebacks).
rem Uses %~dp0 so this folder can move anywhere.
chcp 936 >nul
set PY=python
set APP=%~dp0radar_console.py
if not exist "%APP%" (
  echo [ERROR] radar_console.py not found: %APP%
  pause
  exit /b 1
)
%PY% "%APP%"
echo.
echo exit code %errorlevel%
pause
