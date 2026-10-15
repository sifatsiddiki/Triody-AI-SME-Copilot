@echo off
rem Triody AI on Windows. Needs Python 3.11 or newer from python.org.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from https://www.python.org and tick "Add to PATH".
  pause
  exit /b 1
)
echo Running tests...
%PY% -m unittest -q
if errorlevel 1 (
  echo Tests failed. See the messages above.
  pause
  exit /b 1
)
echo.
set LAN=
set /p LAN="Use Triody on your phone too (same Wi-Fi)? Type y and press Enter, or just press Enter: "
set EXTRA=
if /i "%LAN%"=="y" set EXTRA=--lan
echo Starting the dashboard at http://127.0.0.1:8000  (close this window to stop)
start "" http://127.0.0.1:8000
%PY% -m triody serve %EXTRA%
pause
