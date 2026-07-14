@echo off
REM Install-GUI.bat — build the neuron-gui.exe entry point.
REM Runs pip install -e . (editable) which triggers the [project.gui-scripts]
REM entry in pyproject.toml and creates neuron-gui.exe in the venv's Scripts/.
REM No wheel build, no full installer — just the GUI exe.
setlocal
title Neuron GUI - Install
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM 1) Prefer the installed venv
set "VPY=%LOCALAPPDATA%\Programs\neuron5\.venv\Scripts\python.exe"
if exist "%VPY%" goto install

REM 2) Fall back to system Python
set "VPY=python"
where python >nul 2>nul || set "VPY=py -3"

:install
echo.
echo   Installing neuron-gui.exe ...
echo   Python: %VPY%
echo.

REM Use --find-links vendor so pip picks up the prebuilt pyturso wheel
REM instead of compiling from Rust source.
set "VENDOR=%~dp0vendor"
if exist "%VENDOR%" (
    %VPY% -m pip install -e "%~dp0." --find-links "%VENDOR%"
) else (
    %VPY% -m pip install -e "%~dp0."
)

if errorlevel 1 (
    echo.
    echo *** pip install failed. Scroll up to see the error. ***
    pause
    exit /b 1
)

REM 2) Verify neuron-gui.exe was created
set "GUIEXE=%LOCALAPPDATA%\Programs\neuron5\.venv\Scripts\neuron-gui.exe"
if not exist "%GUIEXE%" (
    echo.
    echo   [!] neuron-gui.exe not found in venv Scripts/.
    echo       The gui-scripts entry may not have been processed.
    echo       Check: pip show neuron ^| findstr gui-scripts
    pause
    exit /b 1
)

REM 3) Create Desktop shortcut
set "SHORTCUT=%USERPROFILE%\Desktop\Neuron GUI.lnk"
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $sc = $ws.CreateShortcut('%SHORTCUT%'); ^
     $sc.TargetPath = '%GUIEXE%'; ^
     $sc.WorkingDirectory = '%LOCALAPPDATA%\Programs\neuron5'; ^
     $sc.Description = 'Neuron - Persistent Semantic Memory'; ^
     $sc.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo ============================================================
    echo   Shortcut created on Desktop: Neuron GUI.lnk
    echo   Target: %GUIEXE%
    echo.
    echo   Double-click the shortcut or run:  python -m neuron gui
    echo ============================================================
) else (
    echo.
    echo   [!] Could not create Desktop shortcut.
    echo       Run manually:  python -m neuron gui
)
pause
