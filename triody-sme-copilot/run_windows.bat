@echo off
rem Triody AI on Windows. Needs Python 3.11 or newer from python.org.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
echo Running tests...
%PY% -m unittest -q
if errorlevel 1 (
  echo Tests failed. See the messages above.
  pause
  exit /b 1
)
echo.
echo Starting the dashboard. Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop.
start "" http://127.0.0.1:8000
%PY% -m triody serve
pause
