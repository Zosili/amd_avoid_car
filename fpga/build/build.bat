@echo off
rem =====================================================================
rem  Rebuild the Vivado project + bitstream from source.
rem
rem  Requirements:
rem    Vivado 2025.2 on PATH, or set VIVADO to vivado.bat full path.
rem  Output:
rem    fpga\src\prj\7020.runs\impl_1\top.bit
rem  Then copy it over fpga\build\top.bit and fpga\pynq\motor.bit.
rem
rem  KEEP PURE ASCII with CRLF line endings.
rem =====================================================================
setlocal

set "HERE=%~dp0"
set "SRC=%HERE%..\src\tcl"
if not defined VIVADO set "VIVADO=vivado"

echo [1/2] create project ...
call "%VIVADO%" -mode batch -source "%SRC%\01_project.tcl"
if errorlevel 1 goto :fail

echo [2/2] synth + impl + bitstream ...
call "%VIVADO%" -mode batch -source "%SRC%\02_build.tcl"
if errorlevel 1 goto :fail

echo.
echo [OK] bitstream: fpga\src\prj\7020.runs\impl_1\top.bit
echo      copy it to fpga\build\top.bit and fpga\pynq\motor.bit
goto :eof

:fail
echo.
echo [ERROR] build failed, see the vivado log above
exit /b 1
