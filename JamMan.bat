@echo off
rem JamMan-Werkzeug starten (Windows). Doppelklick.
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 jamman.py gui
    if errorlevel 1 pause
    goto :ende
)

where python >nul 2>&1
if %errorlevel%==0 (
    python jamman.py gui
    if errorlevel 1 pause
    goto :ende
)

echo.
echo Python 3 wurde nicht gefunden.
echo Bitte von https://www.python.org/downloads/ installieren
echo (im Installer "Add python.exe to PATH" anhaken).
echo.
pause

:ende
