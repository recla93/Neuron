@echo off
REM ============================================================================
REM  Neuron5 "Synapse" - Configuration Center (single entry point)
REM ----------------------------------------------------------------------------
REM  Just double-click this file. It opens an interactive menu that can:
REM    - check your system, install prerequisites, PyTurso and full Neuron5
REM    - add Neuron5 to your AI app (Claude, Cursor, VS Code, OpenCode, ...)
REM      under the 'neuron5' MCP registration key
REM    - connect a Turso Cloud database and launch the HTTP bridge
REM    - run the tests and open the Live Log Console
REM
REM  This .bat is only a thin launcher: it unblocks and runs
REM  scripts\configuration.ps1 with ExecutionPolicy Bypass, using the same
REM  PowerShell host as the rest of the project's tooling.
REM ============================================================================
title Neuron5 (Synapse) - Configuration Center
set "PS1=%~dp0scripts\configuration.ps1"

REM Prefer Windows PowerShell 5.1 (always present); the script self-reinvokes
REM into pwsh if that is the only host available.
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "Get-Item '%PS1%' | Unblock-File -ErrorAction SilentlyContinue; & '%PS1%'"

if ERRORLEVEL 1 (
    echo.
    echo The configuration menu exited with an error. See the messages above.
    pause
)
