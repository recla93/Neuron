@echo off
REM ===================================================================
REM  Neuron - one-click build + install (local/dev convenience)
REM  Double-click this file to:
REM    1. build the vendored pyturso wheel into .\vendor
REM    2. build the Neuron wheel into .\dist
REM    3. run the installer (install.ps1)
REM  No PowerShell typing required.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/4] Ensuring build tooling is present...
python -m pip install --quiet --upgrade build pip
if errorlevel 1 goto :err

echo.
echo [2/4] Building vendored pyturso wheel into .\vendor ...
REM --find-links vendor: prefer an already-vendored prebuilt wheel matching this
REM Python's ABI (e.g. cp314-win_amd64) over compiling from the PyPI sdist. Without
REM this flag pip always rebuilds from source and needs Rust + the MSVC linker.
python -m pip wheel "pyturso==0.6.1" --no-deps --find-links vendor -w vendor
if errorlevel 1 goto :err

echo.
echo [3/4] Building the Neuron wheel into .\dist ...
python -m build
if errorlevel 1 goto :err

echo.
echo [4/4] Running the installer...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -ExecutionPolicy Bypass -NoProfile -Command "Get-Item '%~dp0install.ps1' | Unblock-File -ErrorAction SilentlyContinue; & '%~dp0install.ps1'"
if errorlevel 1 goto :err

echo.
echo ===================================================================
echo  Done. Neuron built and installed.
echo ===================================================================
pause
exit /b 0

:err
echo.
echo *** Something failed above. Scroll up to see the error. ***
echo     See INSTALL.md ^> Troubleshooting for help.
pause
exit /b 1
