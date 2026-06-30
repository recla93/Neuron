@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0run_tests.ps1" %*
exit /b %ERRORLEVEL%
