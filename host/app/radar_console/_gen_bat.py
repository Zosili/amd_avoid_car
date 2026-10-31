# -*- coding: utf-8 -*-
"""一次性生成启动用 .bat（GBK + CRLF，避免中文路径/编码坑）"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SYS_PYW = r"C:\Users\ASUS\AppData\Local\Programs\Python\Python312\pythonw.exe"
SYS_PY = r"C:\Users\ASUS\AppData\Local\Programs\Python\Python312\python.exe"
APP = os.path.join(HERE, "radar_console.py")

MAIN = r'''@echo off
chcp 936 >nul
rem 雷达控制台 —— 双击启动（无黑窗）
set PYW=C:\Users\ASUS\AppData\Local\Programs\Python\Python312\pythonw.exe
set APP=%~dp0radar_console.py
if not exist "%PYW%" (
  echo [ERROR] pythonw.exe not found: %PYW%
  echo edit PYW in this file
  pause
  exit /b 1
)
if not exist "%APP%" (
  echo [ERROR] radar_console.py not found: %APP%
  pause
  exit /b 1
)
start "" "%PYW%" "%APP%"
exit /b 0
'''

DEBUG = r'''@echo off
chcp 936 >nul
rem 调试启动：保留黑窗，能看到报错
set PY=C:\Users\ASUS\AppData\Local\Programs\Python\Python312\python.exe
set APP=%~dp0radar_console.py
"%PY%" "%APP%"
echo.
echo exit code %errorlevel%
pause
'''

for name, text in (("radar_console.bat", MAIN), ("radar_console_debug.bat", DEBUG)):
    p = os.path.join(HERE, name)
    with open(p, "wb") as f:
        f.write(text.replace("\n", "\r\n").encode("gbk"))
    print("written", p, os.path.getsize(p), "B")
