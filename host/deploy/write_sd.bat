@echo off
chcp 936 >nul
title PYNQ write to SD card

net session >nul 2>&1
if not errorlevel 1 goto RUN

echo.
echo   ============================================================
echo    正在请求管理员权限
echo.
echo    屏幕上会弹出 UAC 对话框，请点【是】
echo    如果没看到，检查任务栏是否在闪烁
echo   ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop } catch { Write-Host ''; Write-Host '  提权被取消或失败。' -ForegroundColor Yellow; Write-Host '  请改为：右键 write_sd.bat - 以管理员身份运行' -ForegroundColor Yellow; Start-Sleep -Seconds 8 }"
exit /b

:RUN
echo.
echo   ============================================================
echo    管理员权限已获得，正在启动写卡程序...
echo    接下来会弹出确认框，点【是】才开始写
echo   ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0write_sd.ps1"
echo.
echo   ------------------------------------------------------------
echo    程序已结束。上面的日志可以截图发给我。
echo   ------------------------------------------------------------
pause
exit /b 0
