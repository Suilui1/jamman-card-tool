#!/bin/sh
# JamMan-Werkzeug starten (Linux). Ausführbar machen: chmod +x JamMan.sh
cd "$(dirname "$0")" || exit 1

for P in python3 /usr/bin/python3 /usr/local/bin/python3; do
    command -v "$P" >/dev/null 2>&1 || continue
    if "$P" -c "import tkinter" >/dev/null 2>&1; then
        "$P" jamman.py gui
        if [ $? -ne 0 ]; then
            echo
            echo "Das Werkzeug wurde mit einem Fehler beendet."
            printf "Zum Schliessen die Eingabetaste druecken ... "
            read -r _
        fi
        exit 0
    fi
done

echo "Hinweis: tkinter fehlt (Paket python3-tk). Es geht im Textmenü weiter."
echo
exec python3 jamman.py menu
