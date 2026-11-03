@echo off
chcp 65001 >nul 2>&1
title Car7020 - Fusion Detect
setlocal

rem =====================================================================
rem  Pure ASCII + CRLF only. Chinese text in a .bat breaks the cmd parser
rem  (it desyncs on multibyte bytes and starts executing comment fragments).
rem  All Chinese output comes from Python and is fine under chcp 65001.
rem =====================================================================

set "HERE=%~dp0"
set "APP=%HERE%..\app"
set "SCRIPT=%APP%\detect_fusion_live.py"
set "MODEL=%HERE%..\model\best.onnx"

if not exist "%SCRIPT%" goto :noscript

rem ---- model: pass via CART_MODEL so the script does not need a local models\ ----
if exist "%MODEL%" (
    set "CART_MODEL=%MODEL%"
) else (
    echo [WARN] model not found: %MODEL%
    echo        put best.onnx there, or set CART_MODEL to its path
)

rem ---- python ----
if defined CART_PY set "PY=%CART_PY%"
if not defined PY set "PY=python"
set PYTHONIOENCODING=utf-8

rem ---- board ----
if not defined BOARD_IP set "BOARD_IP=192.168.1.10"
if not defined CMD_PORT set "CMD_PORT=5000"

rem ---- geometry / thresholds (display only, see docs/interface.md) ----
if not defined CAM_HEIGHT set "CAM_HEIGHT=0.20"
if not defined HFOV       set "HFOV=60.0"
if not defined CONF       set "CONF=0.35"

rem ---- radar ----
rem   LIDAR=none  : distance from mono ground estimate only
rem   LIDAR=udp   : take 360-degree scans from the D6 bridge (host\radar)
rem
rem   Angle pairing (see docs/interface.md 5.3):
rem     the bridge outputs 0=front / counter-clockwise positive no matter
rem     whether it runs --mount inverted, so SIGN stays -1 in every case.
rem     Only flip it if the 2-minute box test proves the direction is mirrored.
rem   theta0 must be compensated on ONE side only (default: 0 here, bridge owns it).
if not defined LIDAR      set "LIDAR=none"
if not defined LIDAR_PORT set "LIDAR_PORT=15001"
if not defined SIGN       set "SIGN=-1"
if not defined THETA0     set "THETA0=0.0"

set "LIDAR_ARGS=--lidar %LIDAR%"
if /i "%LIDAR%"=="udp" set "LIDAR_ARGS=--lidar udp --lidar-port %LIDAR_PORT%"

echo ============================================
echo   Car7020  fusion detect
echo   Script : %SCRIPT%
echo   Model  : %CART_MODEL%
echo   Board  : %BOARD_IP%:%CMD_PORT%
echo   Radar  : %LIDAR%  (port %LIDAR_PORT%)
echo   Angle  : theta0=%THETA0%  sign=%SIGN%   ^<- must match the bridge
echo   Camera : cam_height=%CAM_HEIGHT%m  hfov=%HFOV%deg  conf=%CONF%
echo   Press q or Esc to quit
echo ============================================
echo.

pushd "%APP%"
"%PY%" -u "%SCRIPT%" --conf %CONF% --cam-height %CAM_HEIGHT% --hfov %HFOV% --theta0 %THETA0% --sign %SIGN% %LIDAR_ARGS% --color-fix off --cmd-udp %BOARD_IP%:%CMD_PORT% --send
set "EXITCODE=%ERRORLEVEL%"
popd
goto :report

:noscript
echo [ERROR] detect_fusion_live.py not found at %SCRIPT%
set "EXITCODE=9009"
goto :report

:report
echo.
echo ============================================
echo   exited, code = %EXITCODE%
echo   (0 = normal; non-zero = error, see above)
echo ============================================
pause
endlocal
