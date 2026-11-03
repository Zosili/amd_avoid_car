@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title COIN-D6 雷达工具
cd /d "%~dp0"

rem ---- 找 Python：环境变量 PYTHON > 同目录 python.exe > PATH 里的 python > py ----
set "PY="
if defined PYTHON set "PY=%PYTHON%"
if not defined PY if exist "%~dp0python.exe" set "PY=%~dp0python.exe"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    echo 没找到 Python。请装 Python 3.7+ 并勾选 "Add to PATH"，
    echo 或者把 python.exe 放到本文件夹，或设置环境变量 PYTHON。
    pause
    exit /b 1
)

:menu
cls
echo ============================================================
echo   COIN-D6 雷达工具       Python: %PY%
echo   配置: d6_config.json（port 默认 auto，自动找 CH340）
echo   目标: 127.0.0.1:15001  ^<- 队友 detect_fusion_live.py --lidar udp
echo ============================================================
echo.
echo   [1] 列出串口（看雷达插在哪个口）
echo   [2] 自检 3 秒（帧率/圈数/12 扇区/距离帧预览）
echo   [3] 正常运行（发扫描给队友融合）
echo   [4] 正常运行 + 同时给板子发距离帧
echo   [5] 云图（实时窗口）
echo   [6] 云图存一张 PNG（不开窗口）
echo   [7] 云图 demo（没有雷达也能看界面）
echo   [8] 打开交接文档
echo   [0] 退出
echo.
set /p CH=请选择:

if "%CH%"=="1" "%PY%" d6_stream.py --list-ports
if "%CH%"=="2" "%PY%" d6_stream.py --selftest --secs 3
if "%CH%"=="3" "%PY%" d6_stream.py
if "%CH%"=="4" (
    set /p BOIP=请输入板子 IP（回车默认 192.168.1.10）:
    if "!BOIP!"=="" set BOIP=192.168.1.10
    "%PY%" d6_stream.py --dist-udp %BOIP%:5000
)
if "%CH%"=="5" "%PY%" radar_view.py
if "%CH%"=="6" "%PY%" radar_view.py --save cloud.png --secs 4
if "%CH%"=="7" "%PY%" radar_view.py --demo
if "%CH%"=="8" start "" "D6_BRIDGE_NOTES.md"
if "%CH%"=="0" exit /b

echo.
pause
goto menu
