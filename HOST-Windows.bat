@echo off
rem Double-click this to run the server on Windows.
rem Everyone else connects to the address shown in the window that opens.
cd /d "%~dp0"
title Find My Mines - server
echo.
echo    FIND MY MINES - server
echo.

set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython

%PY% -c "import pygame, dotenv, psycopg" >nul 2>nul
if errorlevel 1 (
    echo    Installing dependencies. This happens once and takes a minute...
    echo.
    %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto nopygame
)

echo    Starting the server. Read the address at the top of the window
echo    and send that to the other players. Keep this window open.
echo.
%PY% server.py
goto end

:nopython
echo    Python 3 is not installed on this computer.
echo    Get it from https://www.python.org/downloads/
echo    During the install, tick "Add Python to PATH".
goto end

:nopygame
echo    Could not install dependencies automatically.
echo    Try running this by hand:  python -m pip install -r requirements.txt
goto end

:end
echo.
pause
