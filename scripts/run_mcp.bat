@echo off
REM Neuron v3.3 — MCP stdio launcher
REM Seed knowledge loaded directly from knowledge/base_knowledge.db

set DIR=%~dp0..
set VENV=%DIR%\.venv
set PYTHONPATH=%DIR%\src;%PYTHONPATH%

if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -m neuron
) else (
    python -m neuron
)
