@echo off
rem =====================================================================
rem  Root launcher -> host\deploy\run_fusion.bat
rem
rem  Kept as a THIN WRAPPER so every setting lives in exactly one place.
rem  (The old version hard-coded a miniconda path and a D:\ model path,
rem   which broke the moment the folder was copied to another machine.)
rem
rem  Override anything via environment variables before running:
rem     CART_PY       python interpreter   (default: python on PATH)
rem     CART_MODEL    model .onnx path     (default: host\model\best.onnx)
rem     BOARD_IP      board IP             (default: 192.168.1.10)
rem     CMD_PORT      UDP command port     (default: 5000)
rem     CAM_HEIGHT / HFOV / CONF           geometry + threshold
rem     LIDAR / LIDAR_PORT / THETA0 / SIGN radar options
rem
rem  KEEP PURE ASCII with CRLF line endings.
rem =====================================================================
setlocal

set "HERE=%~dp0"
set "RUNNER=%HERE%host\deploy\run_fusion.bat"

if not exist "%RUNNER%" (
    echo [ERROR] runner not found:
    echo         %RUNNER%
    echo.
    echo   Expected layout:
    echo     car7020\
    echo       host\deploy\run_fusion.bat
    echo       host\app\detect_fusion_live.py
    pause
    exit /b 9009
)

call "%RUNNER%" %*

endlocal
