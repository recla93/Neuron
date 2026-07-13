@echo off
REM Neuron-Setup.bat — double-click launcher for `neuron setup` (install /
REM repair / status / uninstall). Idiot-proof: finds the right Python by
REM itself, keeps the window open at the end.
setlocal
title Neuron Setup
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM 1) Prefer the installed venv (has the neuron package)
set "VPY=%LOCALAPPDATA%\Programs\neuron5\.venv\Scripts\python.exe"
if exist "%VPY%" goto run

REM 2) Fall back to system Python + the source tree next to this file
set "VPY=python"
where python >nul 2>nul || set "VPY=py -3"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

:run
echo.
echo   Neuron Setup  (install / repair / status / uninstall)
echo   Python: %VPY%
echo.
%VPY% -m neuron setup %*
echo.
echo   (finished - exit code %ERRORLEVEL%)
pause
