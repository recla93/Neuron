@echo off
REM Neuron-Manage.bat — double-click launcher for `neuron manage` (overview,
REM export, consolidate, graph visualizer, doctor). Finds the right Python by
REM itself, keeps the window open at the end.
setlocal
title Neuron Manage
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
REM Let `manage --visualize` find the repo's visualizer script
set "NEURON_REPO=%~dp0"

REM 1) Prefer the installed venv (has the neuron package)
set "VPY=%LOCALAPPDATA%\Programs\neuron5\.venv\Scripts\python.exe"
if exist "%VPY%" goto run

REM 2) Fall back to system Python + the source tree next to this file
set "VPY=python"
where python >nul 2>nul || set "VPY=py -3"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

:run
echo.
echo   Neuron Manage  (overview / export / consolidate / visualizer / doctor)
echo   Python: %VPY%
echo.
%VPY% -m neuron manage %*
echo.
echo   (finished - exit code %ERRORLEVEL%)
pause
