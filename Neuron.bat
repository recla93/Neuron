@echo off
REM Neuron.bat — THE one clickable entry point. Interactive menu that routes
REM to Setup (lifecycle), Manage (day-to-day) or the full Configuration
REM Center. Finds the right Python by itself; window never vanishes.
setlocal
title Neuron
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "NEURON_REPO=%~dp0"

set "VPY=%LOCALAPPDATA%\Programs\neuron5\.venv\Scripts\python.exe"
if exist "%VPY%" goto menu
set "VPY=python"
where python >nul 2>nul || set "VPY=py -3"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

:menu
cls
echo.
echo    ============================================
echo      NEURON  -  semantic memory for your AI
echo    ============================================
echo.
echo      1  Setup      (install / repair / uninstall)
echo      2  Manage     (overview / export / visualizer / doctor)
echo      3  Configuration Center  (full menu: bridge, cloud, tests...)
echo      4  Quick health check    (doctor, read-only)
echo      5  Exit
echo.
choice /c 12345 /n /m "   Choose [1-5]: "
if errorlevel 5 goto end
if errorlevel 4 goto doctor
if errorlevel 3 goto center
if errorlevel 2 goto manage
goto setup

:setup
%VPY% -m neuron setup
pause
goto menu

:manage
%VPY% -m neuron manage
pause
goto menu

:center
if exist "%~dp0Configuration.bat" (
    call "%~dp0Configuration.bat"
) else if exist "%~dp0Neuron5Config.bat" (
    call "%~dp0Neuron5Config.bat"
) else (
    echo    [!] Configuration Center not found next to this file.
    pause
)
goto menu

:doctor
%VPY% -m neuron doctor
pause
goto menu

:end
endlocal
