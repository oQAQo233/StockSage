@echo off
rem KGGen 后端的启动脚本：先起本地中转（补 Go 网关要的 session 头），再跑对照。
rem 只用 rule/llm 时不需要这个脚本，直接 run.cmd 即可。
setlocal
cd /d "%~dp0"

set PYTHONPATH=%CD%\src;%CD%\.kgvenv
rem KGGen 一次要输出上百条关系，推理模型会把 token 全耗在 reasoning 上，
rem 导致 dspy 解析到 None（实测 10 篇里 6 篇失败）。这里用不产生 reasoning 的模型。
set NEWSKG_KGGEN_MODEL=openai/claude-haiku-5-5
set NEWSKG_KGGEN_MAX_TOKENS=8192
set NEWSKG_KGGEN_BASE_URL=http://127.0.0.1:8766/v1
set NLTK_DATA=%CD%\.nltk_data
set NLTK_ALLOW_PROXIED_URLOPEN=1

start "kggen-proxy" /min ".venv\Scripts\python.exe" tools\go_proxy.py
timeout /t 2 >nul

".venv\Scripts\python.exe" -u -m newskg.cli --db out\corpus.sqlite3 compare --backends rule,llm,kggen %*
endlocal