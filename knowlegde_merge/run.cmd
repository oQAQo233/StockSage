@echo off
setlocal
set "PYTHONPATH=%~dp0src"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m newskg.cli %*