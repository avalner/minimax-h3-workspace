@echo off
rem Minimax H3 Workspace - run from sources (window mode).
rem Needs Python 3.10+ on PATH and a ComfyUI server at http://127.0.0.1:8188.
setlocal
cd /d "%~dp0app"

python -c "import aiohttp, webview" 2>nul
if errorlevel 1 (
    echo Installing Python dependencies ^(aiohttp, pywebview, certifi^)...
    python -m pip install --upgrade aiohttp pywebview certifi
    if errorlevel 1 goto :fail
)

python main.py
if errorlevel 1 goto :fail
goto :eof

:fail
echo.
echo Could not start. Check that Python 3.10+ is installed and available as "python",
echo then run:  python -m pip install aiohttp pywebview certifi
pause
