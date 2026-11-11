#!/bin/bash
# JamMan-Werkzeug starten (macOS). Doppelklick im Finder.
#
# Gesucht wird ein Python 3 mit Tk 8.6 oder neuer. Apples Python 3.9
# bringt Tk 8.5 mit; das zeichnet auf macOS 26/27 Beschriftungen und
# Listen nicht - das Fenster bleibt leer, nur die Knöpfe sind zu sehen.
# Deshalb: Tk 8.5 wird für das Fenster übergangen, und es geht im
# Textmenü weiter (gleiche Funktionen, nur mit Tastatur).

cd "$(dirname "$0")" || exit 1
KARTE="${KARTE:-}"

for P in /usr/local/bin/python3 /opt/homebrew/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -x "$P" ] || continue
    if "$P" -c "import tkinter,sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)" >/dev/null 2>&1; then
        if [ -n "$KARTE" ]; then
            exec "$P" jamman.py gui --karte "$KARTE"
        fi
        exec "$P" jamman.py gui
    fi
done

echo "Hinweis: Es wurde kein Python mit Tk 8.6 oder neuer gefunden."
echo "Apples Tk 8.5 zeichnet auf diesem macOS keinen Text im Fenster."
echo "Es geht im Textmenü weiter - mit denselben Funktionen."
echo "Für ein Fenster: Python von python.org installieren."
echo
exec /usr/bin/python3 jamman.py menu ${KARTE:+--karte "$KARTE"}
