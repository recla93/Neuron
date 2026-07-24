@echo off
rem Neuron — click-and-go installer (Windows). Double-click me.
rem Runs the unified Gray Matter installer via install.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
