@echo off
REM Neuron v3.3 - MCP stdio launcher
REM Neuron is installed as a package in the venv (no PYTHONPATH hack needed).
REM Seed knowledge ships inside the package (neuron/data/base_knowledge.db).

set DIR=%~dp0..
set VENV=%DIR%\.venv

if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -m neuron
) else (
    python -m neuron
)
