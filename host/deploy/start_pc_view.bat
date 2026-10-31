@echo off
rem PC side: connect to the board monitor port (TCP 5002) and show the radar view.
rem Usage: start_pc_view.bat 192.168.1.10:5002
setlocal
set TARGET=%1
if "%TARGET%"=="" set TARGET=127.0.0.1:5002
set PY=python
set APP=%~dp0..\app\integration\radar_map_view.py
cd /d "%~dp0..\app\integration"
echo [start_pc_view] connecting to %TARGET%
%PY% "%APP%" --tcp %TARGET%
