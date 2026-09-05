@echo off
rem Double-click this to play Find My Mines on Windows.
cd /d "%~dp0"
title Find My Mines
echo.
echo    FIND MY MINES
echo.

set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython

%PY% -c "import pygame" >nul 2>nul
if errorlevel 1 (
    echo    Installing pygame. This happens once and takes a minute...
    echo.
    %PY% -m pip install --quiet --disable-pip-version-check pygame
    if errorlevel 1 goto nopygame
)

echo    Type the server address the host gave you,
echo    or just press Enter to use the one saved in config.py.
echo.
set "ADDR="
set /p ADDR=   Server address:
echo.
if "%ADDR%"=="" (
    %PY% client.py
) else (
    %PY% client.py %ADDR%
)
goto end

:nopython
echo    Python 3 is not installed on this computer.
echo    Get it from https://www.python.org/downloads/
echo    During the install, tick "Add Python to PATH".
goto end

:nopygame
echo    Could not install pygame automatically.
echo    Try running this by hand:  python -m pip install pygame
goto end

:end
echo.
pause
