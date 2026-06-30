@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0neuron-summary.ps1" %*
exit /b %ERRORLEVEL%
