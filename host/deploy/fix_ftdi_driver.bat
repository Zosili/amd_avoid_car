@echo off
chcp 936 >nul
title 修复 FTDI FT2232H 驱动 Error 10
echo ============================================================
echo  修复 FTDI FT2232H   VID_0403 PID_6014
echo  现象: USB Serial Converter 带黄色感叹号, 状态代码 10
echo ============================================================
echo.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 权限不足
    echo        请右键本文件, 选 以管理员身份运行
    echo.
    pause
    exit /b 1
)
echo [1/5] 卸载出问题的设备 ...
pnputil /remove-device "USB\VID_0403&PID_6014\5&274D72C6&0&6"
echo      完成
echo.
echo [2/5] 现在拔掉板子的 USB 线
pause
echo.
echo [3/5] 插回 USB 线, 然后按任意键重新扫描
pause
pnputil /scan-devices
echo      完成
echo.
echo [4/5] 等待 5 秒重新枚举 ...
timeout /t 5 /nobreak >nul
echo.
echo [5/5] 当前串口列表
reg query HKLM\HARDWARE\DEVICEMAP\SERIALCOMM
echo.
echo --- 端口类设备 ---
pnputil /enum-devices /class Ports
echo.
echo ============================================================
echo  如果还是 Error 10:
echo   1 换一根 USB 线 劣质线供电不足
echo   2 换一个 USB 口 优先主板后面的 USB 2.0 口
echo   3 装官方最新驱动 https://ftdichip.com/drivers/vcp-drivers/
echo ============================================================
pause
