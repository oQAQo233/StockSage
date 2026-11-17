@echo off
setlocal
rem kg-gen 及其依赖（litellm / dspy / pyarrow）装在 .kgvenv，与主环境隔开；两个都挂在 PYTHONPATH 上。
set "PYTHONPATH=%~dp0src;%~dp0.kgvenv"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m newskg.cli %*