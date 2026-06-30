@echo off
set "PS1=%~dp0neuron-summary.ps1"
set "ARGS=%*"
powershell -ExecutionPolicy Bypass -NoProfile -Command "Get-Item '%PS1%' | Unblock-File -ErrorAction SilentlyContinue; & '%PS1%' %ARGS%"
if ERRORLEVEL 1 pause
