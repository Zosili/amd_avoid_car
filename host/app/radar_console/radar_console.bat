@echo off
rem Radar console launcher (windowless). Uses %~dp0 so this folder can move anywhere.
rem Requires: a python with tkinter + matplotlib on PATH (or set PYW below).
chcp 936 >nul
set PYW=pythonw
set APP=%~dp0radar_console.py
if not exist "%APP%" (
  echo [ERROR] radar_console.py not found: %APP%
  pause
  exit /b 1
)
where %PYW% >nul 2>nul
if errorlevel 1 (
  echo [ERROR] %PYW% not in PATH. Edit PYW in this file.
  pause
  exit /b 1
)
start "" %PYW% "%APP%"
exit /b 0
