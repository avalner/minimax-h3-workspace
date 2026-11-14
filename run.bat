@echo off
rem AI Video Studio - run from sources (window mode).
rem Needs Python 3.10+ on this computer and a ComfyUI server at http://127.0.0.1:8188.
rem
rem "python" is not always on PATH: the Microsoft Store installs it as an alias that Windows can
rem switch off (Settings -> Apps -> Advanced app settings -> App execution aliases), and then the
rem command answers "Python was not found". So instead of trusting one name, try the usual launchers
rem and keep the first one that really runs; everything below then uses that one.
setlocal
cd /d "%~dp0app"

set "PY="
for %%N in (python python3.13 python3.12 python3.11 python3.10 python3) do (
    if not defined PY (
        %%N -c "import sys" >nul 2>&1 && set "PY=%%N"
    )
)
rem Nothing on PATH (aliases switched off): look where Python is usually installed.
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python" (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
    )
)
if not defined PY if exist "%ProgramFiles%" (
    for /d %%D in ("%ProgramFiles%\Python3*") do (
        if not defined PY if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
    )
)
if not defined PY goto :nopython
echo Python: %PY%

"%PY%" -c "import aiohttp, webview" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies ^(aiohttp, pywebview, certifi^) with %PY%...
    "%PY%" -m pip install --upgrade aiohttp pywebview certifi
    if errorlevel 1 goto :pipfail
)

"%PY%" main.py
if errorlevel 1 goto :crash
goto :eof

:crash
echo.
echo The program stopped with an error. Copy the text above and send it to the author,
echo or press "Report for the developer" in the program's Settings on the next start.
pause
goto :eof

:nopython
echo.
echo Python 3.10+ was not found on this computer.
echo   - Not installed yet: install it from https://www.python.org/downloads/ and tick
echo     "Add python.exe to PATH" in the first screen of the installer.
echo   - Installed from the Microsoft Store: switch its alias back on in
echo     Settings -^> Apps -^> Advanced app settings -^> App execution aliases (python.exe).
pause
goto :eof

:pipfail
echo.
echo Could not install the Python dependencies ^(aiohttp, pywebview, certifi^).
echo Check the internet connection or a proxy, then run this file again.
echo Manual way, in this folder:  "%PY%" -m pip install aiohttp pywebview certifi
pause
goto :eof
