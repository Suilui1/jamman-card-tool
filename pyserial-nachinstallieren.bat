@echo off
chcp 65001 >nul
title JamMan-Werkzeug: USB-Zugang nachinstallieren
echo ============================================================
echo   USB-Zugang fuer das JamMan-Werkzeug nachinstallieren
echo ============================================================
echo.
echo Das Werkzeug spricht das Pedal ueber die serielle Schnittstelle.
echo Dafuer braucht Python das Paket "pyserial". Dieses Fenster
echo installiert es. Es wird nichts anderes veraendert.
echo.
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python wurde nicht gefunden.
  echo Bitte zuerst Python von python.org installieren und dabei
  echo "Add python.exe to PATH" ankreuzen.
  echo.
  pause
  exit /b 1
)
echo Python gefunden. Installiere pyserial ...
echo.
%PY% -m pip install --user --upgrade pyserial
echo.
if errorlevel 1 (
  echo Die Installation ist fehlgeschlagen. Bitte den Text oben notieren.
) else (
  echo Fertig. Bitte das Werkzeug neu starten und "Pedal ueber USB" erneut druecken.
)
echo.
pause
