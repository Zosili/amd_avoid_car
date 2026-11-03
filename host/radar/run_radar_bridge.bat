@echo off
chcp 65001 >nul 2>&1
title COIN-D6 radar bridge
setlocal

rem =====================================================================
rem  Pure ASCII + CRLF only. Chinese in a .bat breaks the cmd parser.
rem  Python prints Chinese; that is fine under chcp 65001.
rem
rem  Usage:
rem    run_radar_bridge.bat                      normal run (sends scans)
rem    run_radar_bridge.bat --selftest --secs 3  health check only
rem    run_radar_bridge.bat --mount normal       if the radar is NOT inverted
rem    run_radar_bridge.bat --mask 137:223       mask the car body sector
rem  Any extra args are passed straight through to d6_stream.py.
rem =====================================================================

cd /d "%~dp0"

if defined CART_PY set "PY=%CART_PY%"
if not defined PY set "PY=python"
set PYTHONIOENCODING=utf-8

rem ---- serial port: override with RADAR_PORT ----
rem   default "auto" = let d6_stream.py pick the CH340 by VID/PID (1A86:7523).
if not defined RADAR_PORT set "RADAR_PORT=auto"

rem ---- angle pairing (see docs/interface.md 5.3) ----
rem   The bridge ALWAYS outputs: 0 deg = car front, counter-clockwise positive.
rem   --mount inverted only compensates the physical flip inside the bridge
rem   (it flips the bridge-internal sign), the OUTPUT convention stays the same.
rem   Therefore the fusion side keeps ang_sign = -1 in every case.
rem   Do NOT set SIGN=+1 on the fusion side just because this is inverted.
if not defined RADAR_MOUNT set "RADAR_MOUNT=inverted"

rem ---- mask: leave empty unless the car body really blocks a sector.
rem   Set RADAR_MASK e.g. "137:223" and it is passed to --mask. ----
if not defined RADAR_MASK set "RADAR_MASK="

echo ============================================
echo   COIN-D6 radar bridge
echo   Port      : %RADAR_PORT%
echo   Mount     : %RADAR_MOUNT%   (output stays: 0=front, CCW+)
echo   Scan UDP  : 127.0.0.1:15001
echo   Distance  : OFF by default (semantics = nearest obstacle, not person)
echo ============================================
echo.

if defined RADAR_MASK (
    "%PY%" -u d6_stream.py --port %RADAR_PORT% --mount %RADAR_MOUNT% --mask %RADAR_MASK% %*
) else (
    "%PY%" -u d6_stream.py --port %RADAR_PORT% --mount %RADAR_MOUNT% %*
)

set "EXITCODE=%ERRORLEVEL%"
echo.
echo ============================================
echo   bridge exited, code = %EXITCODE%
echo ============================================
pause
endlocal
