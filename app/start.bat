@echo off
rem AI Video Studio: needs a running ComfyUI at http://127.0.0.1:8188
cd /d "%~dp0"
python server.py --open
pause
