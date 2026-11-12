#!/usr/bin/env python3
"""JamMan Solo XT — manage the pedal's SD card from a normal computer.

Replacement for DigiTech "JamManager XT" (2012), which crashes on
recent macOS releases as soon as the pedal is detected, and which is no
longer maintained. This tool works directly on the SD card, which the
pedal exposes as a plain drive.

Card layout (verified on real hardware, 2026-10-26):

    JamManSoloXT/
        Patch01/
            patch.xml            name, rhythm, stop mode, patch UUID
            PhraseA/
                phrase.wav        44.1 kHz, 16 bit, stereo
                phrase.xml        IsLoop (single/loop), tempo, phrase UUID
        Patch02/ ...

Commands (without --schreiben nothing is modified — dry run):

    python3 jamman.py liste                list patches (name, length, format)
    python3 jamman.py abspielen --patch 3  play a patch
    python3 jamman.py aufspielen --wav x.wav [--name N] [--stopp fade|instant]
                          [--wiedergabe loop|single] [--rhythmus silence|kick]
    python3 jamman.py aendern --patch 3 [--stopp …] [--wiedergabe …] [--name …]
    python3 jamman.py verschieben --patch 3 --nach 1
    python3 jamman.py loeschen --patch 3
    python3 jamman.py sichern --ziel ORDNER
    python3 jamman.py saeubern            remove macOS ._* files from the card
    python3 jamman.py auswerfen           unmount/eject the card
    python3 jamman.py gui                 simple window (tkinter)
    python3 jamman.py menu                text menu, no tkinter required

Every writing command copies the affected patches to ~/JamMan-Sicherung
first. Command names are German (that is where the tool was written);
the output is German too. See README.md for details.

--- deutsch ---
Ersetzt „JamManager XT" von DigiTech auf macOS, Windows und Linux.
Arbeitet direkt auf der Speicherkarte des JamMan Solo XT. Ohne
--schreiben wird nichts verändert. Vor jedem Schreibvorgang wird nach
~/JamMan-Sicherung gesichert.
"""

import argparse
import math
import os
import re
import shutil
import stat
import string
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from datetime import datetime

# Werte, die auf der Karte tatsaechlich vorkommen (am 26.10.2026 aus allen
# Sicherungen gelesen). Es gibt genau zwei Stopp-Arten und zwei Rhythmen;
# "Finish" aus der alten Oberflaeche ist auf dieser Karte nicht vorhanden und
# wird deshalb NICHT erfunden.
STOPP = {"fade": "FadeOut", "instant": "StopInstantly", "finish": "StopAtEndOfLoop"}
# "Finish" heißt in der Datei StopAtEndOfLoop - gefunden am 10.11.2026 auf
# der Sicherungsplatte (Patch 16, "hot turkey").
RHYTHMUS = {"silence": ("Silence", "0"), "kick": ("StudioKickAndHighHat", "1")}
# "Single" oder "Loop" steht NICHT in patch.xml, sondern in phrase.xml
# unter <IsLoop>. Am 26.10.2026 in allen Sicherungen gefunden: 181× 1,
# 185× 0.
WIEDERGABE = {"loop": "1", "single": "0"}

# Handbuch JamMan Solo XT, Seite 60 (Spezifikationen):
#   "Max. Individual Loop Time: 10 minutes (stereo)"
#   "Loop File Format: 44.1 kHz, 16-bit (stereo), uncompressed WAV"
# Längere Loops nimmt das Pedal nicht an - am 10.11.2026 mit einer
# 15-Minuten-Datei erlebt: auf der Karte vorhanden, im JamMan nicht,
# danach war der Ordner auf dem Mac verschwunden.
MAX_LOOP_SEKUNDEN = 600

WOLL = (44100, 16, 2)          # was der JamMan erwartet: 44,1 kHz, 16 Bit, Stereo

FASSUNG = "3.102 (12.11.2026)"
WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"


def ist_wechselmedium(mountpunkt):
    """Steckt dort ein Wechseldatenträger? (Karte ja, Sicherungsplatte nein.)"""
    if not MAC:
        return False
    try:
        r = subprocess.run(["diskutil", "info", mountpunkt],
                           capture_output=True, text=True)
        return "Removable Media: Removable" in r.stdout
    except Exception:
        return False


def karten_wurzeln():
    """Ordner, in denen ein eingehängter Datenträger liegen kann."""
    if WINDOWS:
        return ["%s:\\" % b for b in string.ascii_uppercase]
    if MAC:
        return ["/Volumes"]
    benutzer = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return ["/run/media/" + benutzer, "/media/" + benutzer,
            "/run/media", "/media", "/mnt", "/Volumes"]


def karten_diagnose():
    """Zeigt, wo gesucht wurde und was dort lag. Für Fehlersuche am Telefon."""
    zeilen = []
    for wurzel in karten_wurzeln():
        if not os.path.isdir(wurzel):
            zeilen.append("  %-28s  (gibt es nicht)" % wurzel)
            continue
        try:
            namen = sorted(os.listdir(wurzel))
        except OSError as e:
            zeilen.append("  %-28s  (nicht lesbar: %s)" % (wurzel, e.strerror))
            continue
        if not namen:
            zeilen.append("  %-28s  (leer)" % wurzel)
            continue
        for name in namen:
            p = os.path.join(wurzel, name)
            if not os.path.isdir(p):
                continue
            unter = []
            try:
                unter = os.listdir(p)
            except OSError as e:
                zeilen.append("  %-28s  (nicht lesbar: %s)" % (p, e.strerror))
                continue
            treffer_name = kartenordner_in(p)
            if treffer_name:
                ordner = os.path.join(p, treffer_name)
                try:
                    anz = len([x for x in os.listdir(ordner)
                               if re.fullmatch(r"Patch\d+", x, re.IGNORECASE)])
                except OSError:
                    anz = -1
                zeilen.append("  %-28s  %s mit %s Patches"
                              % (p, treffer_name, anz if anz >= 0 else "?"))
            elif not MAC and not WINDOWS:
                # Linux hängt manchmal eine Ebene tiefer ein
                for name2 in unter:
                    p2 = os.path.join(p, name2)
                    if not os.path.isdir(p2):
                        continue
                    tiefer = kartenordner_in(p2)
                    if tiefer:
                        zeilen.append("  %-28s  %s gefunden" % (p2, tiefer))
    return zeilen


def karten_kandidaten():
    """Alle Laufwerke mit einem JamManSoloXT-Ordner, das wahrscheinlichste zuerst.

    Anlass 10.11.2026: Auf einer Sicherungsplatte lag ebenfalls ein Ordner
    JamManSoloXT mit 23 alten Patches. Nach dem Alphabet ("2TB-1" vor
    "JAMMAN") wurde die Platte gewählt statt der Karte. Bewertet wird
    deshalb: Name, Wechselmedium, Anzahl der Patches.
    """
    moegliche = []
    for wurzel in karten_wurzeln():
        if not os.path.isdir(wurzel):
            continue
        moegliche.append(wurzel)          # Windows: die Karte IST das Laufwerk
        try:
            namen = sorted(os.listdir(wurzel))
        except OSError:
            continue
        for name in namen:
            p = os.path.join(wurzel, name)
            if not os.path.isdir(p):
                continue
            moegliche.append(p)
            if not WINDOWS and not MAC:
                try:
                    for name2 in sorted(os.listdir(p)):
                        p2 = os.path.join(p, name2)
                        if os.path.isdir(p2):
                            moegliche.append(p2)
                except OSError:
                    pass

    treffer = []
    for p in moegliche:
        try:
            eintraege = os.listdir(p)
        except OSError:
            continue
        for e in eintraege:
            if e.lower() not in [w.lower() for w in KARTENORDNER]:
                continue
            ordner = os.path.join(p, e)
            try:
                anz = len([x for x in os.listdir(ordner)
                           if re.fullmatch(r"Patch\d+", x, re.IGNORECASE)])
            except OSError:
                anz = 0
            if not anz:
                continue
            name = os.path.basename(p.rstrip(os.sep)) or p
            punkte = (100 if "jamman" in name.lower() else 0) \
                   + (50 if ist_wechselmedium(p) else 0) \
                   + min(anz, 40)
            treffer.append((punkte, p, anz, name))
    treffer.sort(reverse=True)
    gesehen = set()
    ergebnis = []
    for _, p, anz, name in treffer:
        if p in gesehen:
            continue
        gesehen.add(p)
        ergebnis.append((p, anz, name))
    return ergebnis


def standard_karte():
    """Wo liegt die Karte?

    macOS    /Volumes/JAMMAN
    Windows  erstes Laufwerk mit einem Ordner JamManSoloXT
    Linux    /media/<benutzer>/<name> oder /run/media/<benutzer>/<name>
    """
    if MAC:
        kandidaten = karten_kandidaten()
        return kandidaten[0][0] if kandidaten else "/Volumes/JAMMAN"
    if WINDOWS:
        for buchstabe in "DEFGHIJKLMNOPQRSTUVWXYZABC":
            p = "%s:\\" % buchstabe
            if kartenordner_in(p):
                return p
        return "D:\\"

    # Linux: die üblichen Einhängepunkte absuchen
    kandidaten = []
    for wurzel in ("/run/media/" + os.environ.get("USER", ""),
                   "/media/" + os.environ.get("USER", ""),
                   "/run/media", "/media", "/mnt"):
        if not os.path.isdir(wurzel):
            continue
        for name in sorted(os.listdir(wurzel)):
            p = os.path.join(wurzel, name)
            if kartenordner_in(p):
                kandidaten.append(p)
    return kandidaten[0] if kandidaten else "/media"


def volumen_pfad(pfad):
    """Wurzel des Datenträgers. Funktioniert auf beiden Systemen und ist
    auch auf dem Mac prüfbar:

        /Volumes/JAMMAN/JamManSoloXT   ->  /Volumes/JAMMAN
        D:\\JamManSoloXT               ->  D:\\
    """
    roh = os.path.expanduser(str(pfad))
    m = re.match(r"^([A-Za-z]):[\\/]", roh)      # Windows-Laufwerk
    if m:
        return m.group(1).upper() + ":" + os.sep
    pfad = os.path.abspath(roh)
    teile = [x for x in pfad.split(os.sep) if x != ""]
    # macOS:      /Volumes/<name>/…
    if len(teile) >= 2 and teile[0] == "Volumes":
        return "/Volumes/" + teile[1]
    # Linux:      /media/<benutzer>/<name>/…
    #             /run/media/<benutzer>/<name>/…
    if len(teile) >= 3 and teile[0] == "media":
        return "/media/%s/%s" % (teile[1], teile[2])
    if len(teile) >= 4 and teile[0] == "run" and teile[1] == "media":
        return "/run/media/%s/%s" % (teile[2], teile[3])
    return pfad


def abspielen_datei(pfad):
    """Ton starten. Gibt ein Objekt zurück, mit dem gestoppt werden kann."""
    if WINDOWS:
        import winsound
        winsound.PlaySound(pfad, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return "winsound"
    if MAC:
        return subprocess.Popen(["afplay", pfad])

    # Linux: das erste Programm, das da ist
    for befehl in (["aplay", "-q", pfad],
                   ["paplay", pfad],
                   ["mpv", "--no-video", "--really-quiet", pfad],
                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", pfad],
                   ["cvlc", "--play-and-exit", "--intf", "dummy", pfad]):
        if shutil.which(befehl[0]):
            return subprocess.Popen(befehl, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
    raise SystemExit("Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay).")


def stoppen_ton(griff):
    if guff := griff:
        if guff == "winsound":
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        elif hasattr(guff, "poll") and guff.poll() is None:
            guff.terminate()
            try:
                guff.wait(timeout=2)
            except Exception:
                guff.kill()


def ton_laeuft(griff):
    if not griff:
        return False
    if not hasattr(griff, "poll"):
        # Windows (winsound) meldet nichts zurück. Die Oberfläche rechnet
        # dort mit der bekannten Länge weiter.
        return True
    return griff.poll() is None


def fade_ausblenden(pfad, sekunden):
    """Blendet die letzten Sekunden einer WAV-Datei aus (linear).

    Das Pedal blendet beim Stoppen fest über 10 Sekunden aus (Handbuch
    JamMan Solo XT, Seite 47); eine einstellbare Fade-Dauer kennt es
    nicht. Wer eine andere Länge will, muss sie in die Aufnahme legen -
    dann ist sie unabhängig vom Stopp-Verhalten.
    """
    import wave as _wave
    import array
    if sekunden <= 0:
        return False
    with _wave.open(pfad, "rb") as w:
        kanaele, breite, rate, anz = (w.getnchannels(), w.getsampwidth(),
                                      w.getframerate(), w.getnframes())
        rahmen = w.readframes(anz)
    if breite != 2:
        return False
    a = array.array("h")
    a.frombytes(rahmen)
    pro_frame = len(a) // anz if anz else kanaele
    n = int(rate * sekunden) * pro_frame
    n = min(n, len(a))
    if n <= 0:
        return False
    for i in range(n):
        faktor = (n - i) / float(n)
        stelle = len(a) - n + i
        a[stelle] = int(a[stelle] * faktor)
    with _wave.open(pfad, "wb") as w:
        w.setnchannels(kanaele); w.setsampwidth(breite); w.setframerate(rate)
        w.writeframes(a.tobytes())
    return True


def pegel_info(pfad):
    """Spitzen- und Effektivpegel einer WAV-Datei in dBFS."""
    import wave as _wave
    import array
    import math
    with _wave.open(pfad, "rb") as w:
        rahmen = w.readframes(w.getnframes())
    a = array.array("h"); a.frombytes(rahmen)
    if not len(a):
        return None, None
    spitze = max(abs(x) for x in a)
    summe = 0
    for x in a:
        summe += x * x
    rms = math.sqrt(summe / len(a))
    def db(wert):
        return 20 * math.log10(wert / 32768.0) if wert > 0 else -999.0
    return db(spitze), db(rms)


def pegel_anpassen(pfad, db=None, ziel_spitze_db=None, begrenzen=True,
                   grenze_db=0.0, ziel_rms_db=None):
    """Lautstärke ändern: um db verstärken/absenken oder auf einen
    Spitzenpegel normieren.

    begrenzen=True hält die Spitze unter grenze_db (Vorgabe 0 dBFS):
    Statt zu übersteuern wird die Anhebung zurückgenommen. Sonst würde
    alles über 0 dBFS abgeschnitten - das hört man als Kratzen.
    Gibt ein Wörterbuch mit vorher, nachher, geklippt, begrenzt zurück.
    """
    import wave as _wave
    import array
    import math
    with _wave.open(pfad, "rb") as w:
        kanaele, breite, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        rahmen = w.readframes(w.getnframes())
    if breite != 2:
        return None
    a = array.array("h"); a.frombytes(rahmen)
    if not len(a):
        return None
    alt_spitze = max(abs(x) for x in a)
    if alt_spitze == 0:
        return None
    if ziel_spitze_db is not None:
        ziel = 32768.0 * (10 ** (ziel_spitze_db / 20.0))
        faktor = ziel / alt_spitze
    elif ziel_rms_db is not None:
        # Auf gleiche Lautheit (Effektivwert) normieren - das entspricht
        # eher dem, was man als gleich laut hört, als die Spitze.
        summe = 0
        for x in a:
            summe += x * x
        rms = math.sqrt(summe / len(a))
        if rms == 0:
            return None
        ziel = 32768.0 * (10 ** (ziel_rms_db / 20.0))
        faktor = ziel / rms
    else:
        faktor = 10 ** (db / 20.0)

    begrenzt = False
    if begrenzen:
        grenze = 32767.0 * (10 ** (grenze_db / 20.0))
        if alt_spitze * faktor > grenze:
            faktor = grenze / alt_spitze
            begrenzt = True
    geklippt = 0
    for i in range(len(a)):
        wert = int(a[i] * faktor)
        if wert > 32767:
            wert = 32767; geklippt += 1
        elif wert < -32768:
            wert = -32768; geklippt += 1
        a[i] = wert
    with _wave.open(pfad, "wb") as w:
        w.setnchannels(kanaele); w.setsampwidth(breite); w.setframerate(rate)
        w.writeframes(a.tobytes())
    neu_spitze = max(abs(x) for x in a)
    vorher = 20 * math.log10(alt_spitze / 32768.0)
    nachher = 20 * math.log10(neu_spitze / 32768.0) if neu_spitze else -999
    return {"vorher": vorher, "nachher": nachher,
            "geklippt": geklippt, "begrenzt": begrenzt}


def schreibe_pegelprotokoll(basis, eintraege, grund, wurzel=None):
    """Schreibt auf, was am Pegel geändert wurde.

    Damit lässt sich eine Pegeländerung auch dann zurückrechnen, wenn die
    große Sicherung einmal fehlt: Faktor umkehren, fertig. Die Rundung
    auf 16 Bit kostet dabei höchstens eine Stufe.
    """
    if not eintraege:
        return None
    ordner = os.path.expanduser(wurzel or SICHERUNG_WURZEL)
    try:
        os.makedirs(ordner, exist_ok=True)
        ziel = freier_name(os.path.join(
            ordner, "Pegelprotokoll_%s_%s.txt"
            % (datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), grund)))
        f = open(ziel, "w", encoding="utf-8")
    except OSError:
        print(T("  Hinweis: Pegelprotokoll konnte nicht geschrieben werden."))
        return None
    with f:
        f.write("# Pegeländerung %s (%s)\n" % (
            datetime.now().isoformat(timespec="seconds"), grund))
        f.write("# Karte: %s\n" % basis)
        f.write("# Faktor = Verstärkung, die angewendet wurde.\n")
        f.write("# Ordner\tName\tFaktor\tLautheit vorher\tLautheit nachher\n")
        for e in eintraege:
            f.write("%s\t%s\t%.6f\t%+.2f\t%+.2f\n" % (
                e["ordner"], e["name"], e["faktor"], e["vorher"], e["nachher"]))
    print(T("  Protokoll: %s") % ziel)
    return ziel


def lies_pegelprotokoll(pfad):
    """Liest ein Pegelprotokoll: Liste aus Ordner, Faktor je Zeile.

    Fehler werden als SystemExit gemeldet, nicht als Ausnahme: sonst sah
    der Nutzer bei einer falsch gewählten Datei einen Traceback statt eines
    Satzes (gefunden am 11.11.2026: eine WAV als Protokoll gewählt).
    """
    try:
        with open(pfad, encoding="utf-8") as datei:
            zeilen = datei.readlines()
    except FileNotFoundError:
        raise SystemExit("Fehler: '%s' gibt es nicht." % pfad)
    except IsADirectoryError:
        raise SystemExit("Fehler: '%s' ist ein Ordner, keine Datei." % pfad)
    except UnicodeDecodeError:
        raise SystemExit("Fehler: '%s' ist kein Pegelprotokoll "
                         "(keine lesbare Textdatei)." % pfad)
    except PermissionError:
        raise SystemExit("Fehler: '%s' darf nicht gelesen werden." % pfad)
    except OSError as fehler:
        raise SystemExit("Fehler: '%s' lässt sich nicht lesen: %s"
                         % (pfad, fehler))
    eintraege = []
    for zeile in zeilen:
        if zeile.startswith("#") or not zeile.strip():
            continue
        teile = zeile.rstrip("\n").split("\t")
        if len(teile) < 3:
            continue
        try:
            eintraege.append({"ordner": teile[0], "name": teile[1],
                              "faktor": float(teile[2])})
        except ValueError:
            continue
    return eintraege


def befehl_pegel_umkehren(basis, protokoll, schreiben, wurzel=None):
    """Pegeländerung aus einem Protokoll zurückrechnen."""
    eintraege = lies_pegelprotokoll(protokoll)
    if not eintraege:
        raise SystemExit("Fehler: im Protokoll stehen keine Einträge.")
    print(T("  Protokoll: %s") % protokoll)
    print(T("  %d Einträge") % len(eintraege))
    if not schreiben:
        for e in eintraege:
            print(T("    %-10s %-24s Faktor %.4f → 1/%.4f") % (
                e["ordner"], e["name"][:24], e["faktor"], e["faktor"]))
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return
    ziel = sicher_ablegen(basis, [pf for _, _, pf in patches(basis)],
                          "vor_pegel_umkehren", wurzel)
    gemacht = 0
    for e in eintraege:
        wav = os.path.join(basis, e["ordner"], "PhraseA", "phrase.wav")
        if not os.path.exists(wav) or e["faktor"] <= 0:
            continue
        ergebnis = pegel_anpassen(wav, db=-20.0 * math.log10(e["faktor"]),
                                  begrenzen=False)
        if ergebnis:
            gemacht += 1
            print(T("    %-10s %+6.1f → %+6.1f dB") % (
                e["ordner"], ergebnis["vorher"], ergebnis["nachher"]))
    print(T("  Zurückgerechnet: %d Patches. Sicherung davor: %s") % (gemacht, ziel))


def print_pegel(ergebnis):
    """Meldet, was mit dem Pegel passiert ist."""
    print(T("  Pegel:        %.1f dB → %.1f dB Spitze") % (
        ergebnis["vorher"], ergebnis["nachher"]))
    if ergebnis["begrenzt"]:
        print(T("                Die Anhebung wurde begrenzt, damit nichts"))
        print(T("                über 0 dBFS geht (sonst würde übersteuert)."))
    if ergebnis["geklippt"]:
        print(T("                Achtung: %d Werte liegen an der Grenze.") % ergebnis["geklippt"])


def unlesbar_hinweis(pfad):
    """Erklärt, warum eine Datei nicht lesbar ist - und was hilft."""
    endung = os.path.splitext(str(pfad))[1].lower()
    if endung in (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"):
        if werkzeug_pfad("ffmpeg") is None:
            return ("\n  Dieses Format kann nur mit ffmpeg gelesen werden.\n"
                    + ffmpeg_hinweis())
        return "\n  ffmpeg ist vorhanden, konnte die Datei aber nicht lesen.\n"
    return ""


def ffmpeg_fehlt():
    """Wird ffmpeg gebraucht und ist es nicht da?

    Auf dem Mac übernimmt afconvert; dort ist ffmpeg nicht nötig.
    """
    if MAC:
        return False
    return werkzeug_pfad("ffmpeg") is None


def ffmpeg_anleitung():
    """Anleitung zum Nachlesen - Bildschirm und Datei nutzen denselben Text."""
    if WINDOWS:
        schritte = r"""MP3 und M4A brauchen ffmpeg

Das JamMan Solo XT nimmt nur WAV (44,1 kHz, 16 Bit, Stereo).
Zum Umwandeln von MP3, M4A, AAC, FLAC oder OGG braucht dieses
Werkzeug unter Windows das Programm ffmpeg.

Weg 1 - mit Windows-Bordmitteln (empfohlen)

  1. Startmenu -> "Eingabeaufforderung" oder "PowerShell" öffnen
  2. Diesen Befehl eintippen und Enter drücken:

         winget install --id Gyan.FFmpeg

  3. Warten, bis "Successfully installed" erscheint
  4. Das JamMan-Werkzeug neu starten

Weg 2 - ohne Installation

  1. https://www.gyan.dev/ffmpeg/builds/ aufrufen
  2. "ffmpeg-release-essentials.zip" herunterladen
  3. Entpacken, im Ordner bin liegt ffmpeg.exe
  4. Diese ffmpeg.exe neben jamman.py kopieren (gleicher Ordner)

Weg 3 - von Hand

  ffmpeg.exe nach C:\ffmpeg\bin\ legen - dieser Ort wird auch gefunden,
  ohne dass etwas an PATH geändert werden muss.

Prüfen, ob es geklappt hat

  In der Eingabeaufforderung:   ffmpeg -version
  Oder im Werkzeug:             jamman.py selbsttest
  Dort steht dann eine Zeile "ffmpeg: C:\...\ffmpeg.exe".

Ohne ffmpeg geht weiterhin alles mit WAV-Dateien."""
    else:
        schritte = r"""MP3 und M4A brauchen ffmpeg

Das JamMan Solo XT nimmt nur WAV (44,1 kHz, 16 Bit, Stereo).
Zum Umwandeln anderer Formate braucht das Werkzeug ffmpeg.

  Debian/Ubuntu:   sudo apt install ffmpeg
  Fedora:          sudo dnf install ffmpeg
  Arch:            sudo pacman -S ffmpeg
  openSUSE:        sudo zypper install ffmpeg

Ohne ffmpeg geht weiterhin alles mit WAV-Dateien."""
    return schritte


def ffmpeg_hinweis():
    """Sagt, wie ffmpeg auf diesen Rechner kommt."""
    if WINDOWS:
        return ("  Windows:  winget install --id Gyan.FFmpeg\n"
                "            oder ffmpeg.exe neben jamman.py legen\n")
    if MAC:
        return ("  macOS:    brew install ffmpeg   (meist nicht nötig,\n"
                "            afconvert ist eingebaut)\n")
    return "  Linux:    sudo apt install ffmpeg\n"


def werkzeug_pfad(name):
    """Externes Programm finden: PATH, dann der eigene Ordner, dann übliche Orte.

    Windows-Nutzer legen ffmpeg.exe oft einfach neben das Werkzeug oder nach
    C:\ffmpeg\bin - beides wird gefunden, ohne PATH-Umbau.
    """
    endung = ".exe" if WINDOWS else ""
    treffer = shutil.which(name)
    if treffer:
        return treffer
    ordner = os.path.dirname(os.path.abspath(__file__))
    moeglich = [os.path.join(ordner, name + endung),
                os.path.join(ordner, "ffmpeg", "bin", name + endung)]
    if WINDOWS:
        moeglich += [os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                  "Microsoft", "WinGet", "Links", name + ".exe"),
                     "C:\\ffmpeg\\bin\\" + name + ".exe",
                     os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"),
                                  "ffmpeg", "bin", name + ".exe")]
    else:
        moeglich += ["/usr/local/bin/" + name, "/opt/homebrew/bin/" + name,
                     "/usr/bin/" + name, "/snap/bin/" + name]
    for pfad in moeglich:
        if os.path.isfile(pfad):
            return pfad
    return None


def neue_tempdatei(prefix="jamman-", suffix=".wav"):
    """Temporäre Datei anlegen und den Handle sofort schließen.

    tempfile.mkstemp gibt einen offenen Handle zurück. Wird er nicht
    geschlossen, lässt Windows die Datei nicht löschen und sperrt sie
    unter Umständen (am 10.11.2026 auf Windows 10 aufgefallen).
    """
    griff, pfad = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(griff)
    return pfad


def wav_kopf(pfad):
    """Liest die Kopfangaben einer WAV-Datei: Format, Kanäle, Rate, Bits,
    Position und Länge der Audiodaten. Gibt None zurück, wenn es keine
    lesbare WAV-Datei ist."""
    import struct
    try:
        f = open(pfad, "rb")
    except OSError:
        return None
    with f:
        kopf = f.read(12)
        if len(kopf) < 12 or kopf[0:4] != b"RIFF" or kopf[8:12] != b"WAVE":
            return None
        while True:
            k = f.read(8)
            if len(k) < 8:
                return None
            name = k[0:4]
            groesse = struct.unpack("<I", k[4:8])[0]
            if name == b"fmt ":
                fmt = f.read(groesse)
                if len(fmt) < 16:
                    return None
                format_, kanaele, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[0:16])
                return {"format": format_, "kanaele": kanaele, "rate": rate,
                        "bits": bits, "block": block, "fmt_groesse": groesse,
                        "daten_start": None, "daten_laenge": None}
            if name == b"data":
                stelle = f.tell()
                return {"format": None, "daten_start": stelle, "daten_laenge": groesse}
            f.seek(groesse + (groesse % 2), 1)      # gerade ausrichten
    return None


def normalisiere_wav(pfad):
    """Schreibt den WAV-Kopf auf einfaches PCM um (Format 1, 16-Byte-fmt).

    Grund: afconvert erzeugt bei manchen Quellen (z. B. M4A mit
    Kanallayout) WAVE_FORMAT_EXTENSIBLE (0xFFFE, 40-Byte-fmt). Der JamMan
    erkennt solche Patches nicht - am 10.11.2026 bei Patch 29 gesehen.
    Die Audiodaten bleiben unverändert, nur der Kopf wird neu geschrieben.
    Gibt True zurück, wenn umgeschrieben wurde.
    """
    import struct
    angaben = wav_kopf(pfad)
    if not angaben or angaben.get("format") is None:
        return False
    if angaben["format"] == 1 and angaben["fmt_groesse"] == 16:
        return False                                   # schon in Ordnung

    # Datenbereich suchen (fmt kann vor data stehen, muss aber nicht)
    with open(pfad, "rb") as f:
        f.seek(12)
        start = laenge = None
        fmt = None
        while True:
            k = f.read(8)
            if len(k) < 8:
                break
            name, groesse = k[0:4], struct.unpack("<I", k[4:8])[0]
            if name == b"fmt ":
                fmt = f.read(groesse)
            elif name == b"data":
                start, laenge = f.tell(), groesse
                break
            else:
                f.seek(groesse + (groesse % 2), 1)
        if fmt is None or start is None:
            return False
        format_, kanaele, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[0:16])
        if format_ not in (1, 0xFFFE):
            print(T("  Hinweis: unbekanntes WAV-Format 0x%04X - nicht angefasst.") % format_)
            return False

    zwischendatei = pfad + ".pcm"
    with open(pfad, "rb") as quelle, open(zwischendatei, "wb") as ziel:
        datenlaenge = min(laenge, os.path.getsize(pfad) - start)
        kopf = b"RIFF" + struct.pack("<I", 36 + datenlaenge) + b"WAVE"
        kopf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, kanaele, rate,
                                     rate * block, block, bits)
        kopf += b"data" + struct.pack("<I", datenlaenge)
        ziel.write(kopf)
        quelle.seek(start)
        rest = datenlaenge
        while rest > 0:
            stueck = quelle.read(min(1 << 20, rest))
            if not stueck:
                break
            ziel.write(stueck)
            rest -= len(stueck)
    os.replace(zwischendatei, pfad)
    return True


def kuerze_wav(pfad, sekunden, blende=0.012):
    """Schneidet eine WAV-Datei nach der angegebenen Länge ab.

    "Übergangslos" heißt hier: an der Schnittstelle wird nichts eingefügt,
    keine Pause, kein neuer Ton. Damit es dabei nicht knackt, werden die
    letzten Millisekunden kurz ausgeblendet (Vorgabe 12 ms) - das ist
    kürzer als ein Wimpernschlag und hört sich als glatter Übergang an.
    Der WAV-Kopf wird dabei gleich mit auf einfaches PCM gesetzt.
    """
    angaben = wav_kopf(pfad)
    if not angaben or angaben.get("format") is None:
        return False
    # Datenbereich finden
    with open(pfad, "rb") as f:
        f.seek(12)
        fmt = None
        start = laenge = None
        while True:
            k = f.read(8)
            if len(k) < 8:
                break
            name, groesse = k[0:4], struct.unpack("<I", k[4:8])[0]
            if name == b"fmt ":
                fmt = f.read(groesse)
            elif name == b"data":
                start, laenge = f.tell(), groesse
                break
            else:
                f.seek(groesse + (groesse % 2), 1)
        if fmt is None or start is None:
            return False
        format_, kanaele, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[0:16])
        if format_ not in (1, 0xFFFE) or bits != 16:
            return False
        max_bytes = int(sekunden * rate) * block
        behalten = min(laenge, max_bytes)
        behalten -= behalten % block
        quelle_daten = f.read(behalten)

    import array
    werte = array.array("h")
    werte.frombytes(quelle_daten)
    blende_frames = int(rate * blende) * kanaele
    n = min(blende_frames, len(werte))
    for i in range(n):
        faktor = (n - i) / float(n)
        stelle = len(werte) - n + i
        werte[stelle] = int(werte[stelle] * faktor)

    zwischendatei = pfad + ".kurz"
    with open(zwischendatei, "wb") as ziel:
        daten = werte.tobytes()
        kopf = b"RIFF" + struct.pack("<I", 36 + len(daten)) + b"WAVE"
        kopf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, kanaele, rate,
                                     rate * block, block, 16)
        kopf += b"data" + struct.pack("<I", len(daten))
        ziel.write(kopf)
        ziel.write(daten)
    os.replace(zwischendatei, pfad)
    return True


def wandle_nach_44k(quelle, ziel):
    """Auf 44,1 kHz, 16 Bit, Stereo bringen. Reihenfolge:
    afconvert (nur Mac) -> ffmpeg -> reines Python über das wave-Modul."""
    if MAC:
        r = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100",
                            "-c", "2", quelle, ziel], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(ziel):
            return True
    ffmpeg = werkzeug_pfad("ffmpeg")
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                            "-i", quelle, "-ar", "44100", "-ac", "2",
                            "-c:a", "pcm_s16le", ziel], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(ziel):
            return True
    # Letzter Weg: selbst umrechnen (nur WAV, nur Kanäle/Dopplung)
    try:
        import wave as _wave
        with _wave.open(quelle, "rb") as q:
            kanaele, breite, rate = q.getnchannels(), q.getsampwidth(), q.getframerate()
            rahmen = q.readframes(q.getnframes())
        if rate != 44100 or breite != 2:
            return False          # ohne Bibliothek nicht sauber möglich
        if kanaele == 1:
            import audioop
            rahmen = audioop.tostereo(rahmen, 2, 1, 1)
        elif kanaele > 2:
            import audioop
            rahmen = audioop.tomono(rahmen, 2, 0.5, 0.5)
        with _wave.open(ziel, "wb") as z:
            z.setnchannels(2); z.setsampwidth(2); z.setframerate(44100)
            z.writeframes(rahmen)
        return True
    except Exception:
        return False


def auswerfen_datentraeger(karte):
    """Karte auswerfen. Gibt (erfolg, meldung) zurück."""
    wurzel = volumen_pfad(karte)
    if not os.path.isdir(wurzel):
        return False, "ist nicht eingelegt"
    if MAC:
        r = subprocess.run(["diskutil", "eject", wurzel], capture_output=True, text=True)
        return (r.returncode == 0,
                "Karte ausgeworfen." if r.returncode == 0
                else (r.stderr.strip() or r.stdout.strip()))
    if not (MAC or WINDOWS):
        # Linux: Gerät zur Einhängestelle finden, dann über udisks2 abmelden
        r = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "--target", wurzel],
                           capture_output=True, text=True)
        geraet = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if geraet and shutil.which("udisksctl"):
            r = subprocess.run(["udisksctl", "unmount", "-b", geraet],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return True, "Karte abgemeldet."
        r = subprocess.run(["umount", wurzel], capture_output=True, text=True)
        if r.returncode == 0:
            return True, "Karte abgemeldet."
        return False, (r.stderr.strip() or "umount braucht vermutlich root")

    if WINDOWS:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(New-Object -ComObject Shell.Application).Namespace(17)"
                            ".ParseName('%s').InvokeVerb('Eject')" % wurzel.rstrip("\\")],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True, "Karte ausgeworfen."
        r2 = subprocess.run(["mountvol", wurzel.rstrip("\\"), "/P"],
                            capture_output=True, text=True)
        if r2.returncode == 0:
            return True, "Laufwerk abgemeldet. Karte kann abgezogen werden."
        return False, (r2.stderr.strip() or r.stderr.strip() or "unbekannter Fehler")
    return False, "Auswerfen wird auf diesem System nicht unterstützt"

UNTERORDNER = "JamManSoloXT"
# Ein JamMan legt auf der Karte einen Ordner mit seinem Gerätenamen an.
# Solo XT und Stereo sind darunter gleich aufgebaut (Patch01 … / PhraseA /
# patch.xml). Belegt am 12.11.2026: ein Nutzer im Bassic-Forum hat den
# Stereo erreicht, indem er im Skript „SoloXT" durch „Stereo" ersetzte.
KARTENORDNER = ("JamManSoloXT", "JamManStereo")


def schrift(groesse, fest=False, titel=False):
    """Passende Schrift je System.

    Menlo gibt es nur auf dem Mac; Windows und Linux brauchen eigene
    Namen, sonst wählt Tk irgendetwas und die Spalten passen nicht.
    """
    if MAC:
        name = "Helvetica" if titel else "Menlo"
    elif WINDOWS:
        name = "Segoe UI" if titel else "Consolas"
    else:
        name = "DejaVu Sans" if titel else "DejaVu Sans Mono"
    return (name, groesse, "bold") if fest else (name, groesse)


# ---------------------------------------------------------------- Grundlagen

def kartenordner_in(p):
    """Name des JamMan-Ordners in p, oder None. Groß/klein ist egal."""
    try:
        eintraege = os.listdir(p)
    except OSError:
        return None
    for wunsch in KARTENORDNER:
        for e in eintraege:
            if e.lower() == wunsch.lower() and os.path.isdir(os.path.join(p, e)):
                return e
    return None


def karte_pruefen(pfad):
    """Gibt den Ordner des Geräts zurück oder bricht mit klarer Meldung ab.

    Der Ordner heißt je nach Gerät "JamManSoloXT" oder "JamManStereo";
    darunter ist der Aufbau gleich. Welcher gefunden wurde, steht danach
    in UNTERORDNER - alle späteren Pfade bauen darauf auf.
    """
    global UNTERORDNER
    p = os.path.abspath(os.path.expanduser(pfad))
    if not os.path.isdir(p):
        raise SystemExit(T("Fehler: '%s' ist kein Ordner. Ist die Karte eingelegt?") % pfad)
    # Wurde schon der Geräteordner selbst gewählt? Dann direkt nehmen.
    for wunsch in KARTENORDNER:
        if os.path.basename(p).lower() == wunsch.lower():
            UNTERORDNER = os.path.basename(p)
            return p
    gefunden = kartenordner_in(p)
    if gefunden:
        UNTERORDNER = gefunden
        return os.path.join(p, gefunden)
    raise SystemExit(T("Fehler: auf '%s' liegt kein Ordner '%s' oder '%s'. "
                         "Das ist keine JamMan-Karte.") % (p, KARTENORDNER[0], KARTENORDNER[1]))


def ist_ordner(pfad):
    """Ist das ein Ordner? Mit Rückfallweg.

    Ubuntu Studio meldete am 10.11.2026 für die Patch-Ordner einer
    JamMan-Karte "Datei" - os.path.isdir sah also kein Verzeichnis,
    obwohl eines da ist. Deshalb zusätzlich an den Dateien erkennen,
    die in einem Patch liegen müssen.
    """
    try:
        return stat.S_ISDIR(os.stat(pfad).st_mode)
    except OSError:
        pass
    for probe in ("patch.xml", "PhraseA/phrase.xml", "PhraseA/phrase.wav"):
        if os.path.exists(os.path.join(pfad, *probe.split("/"))):
            return True
    return False


def karte_lesefehler(basis):
    """Zählt Einträge, deren Zustand das System nicht lesen kann.

    Am 10.11.2026 auf Ubuntu Studio: die Karte war eingehängt, das
    Verzeichnis ließ sich auflisten, aber jedes stat() auf die
    Patch-Ordner endete mit "Input/output error". Das ist ein Fehler des
    Dateisystems oder der Karte - nicht des Werkzeugs.
    """
    fehler = []
    try:
        namen = os.listdir(basis)
    except OSError as e:
        return ["(Verzeichnis nicht lesbar: %s)" % e.strerror]
    for name in namen:
        if not re.fullmatch(r"Patch\d+", name, re.IGNORECASE):
            continue
        try:
            os.stat(os.path.join(basis, name))
        except OSError as e:
            fehler.append("%s: %s" % (name, e.strerror))
    return fehler


def patches(basis):
    """Alle Patch-Ordner, numerisch sortiert."""
    gefunden = []
    for name in os.listdir(basis):
        if not re.fullmatch(r"Patch\d+", name, re.IGNORECASE):
            continue
        pfad = os.path.join(basis, name)
        if not ist_ordner(pfad):
            continue
        nummer = int(name[5:])
        gefunden.append((nummer, name, pfad))
    return sorted(gefunden)


def patch_name(pfad):
    """<PatchName> aus patch.xml, sonst der Ordnername."""
    q = os.path.join(pfad, "patch.xml")
    if not os.path.exists(q):
        return "(ohne patch.xml)"
    try:
        baum = ET.parse(q)
        for el in baum.iter():
            if el.tag.endswith("PatchName"):
                return (el.text or "").strip() or "(ohne Namen)"
    except ET.ParseError as e:
        return "(patch.xml nicht lesbar: %s)" % e
    return "(ohne Namen)"


def patch_einstellungen(pfad):
    """Stopp-Verhalten, Rhythmus und Loop/Single eines Patches, kurz."""
    stop, rhy, loop = "?", "?", "?"
    px = os.path.join(pfad, "patch.xml")
    ph = os.path.join(pfad, "PhraseA", "phrase.xml")
    try:
        t = open(px, encoding="utf-8", errors="replace").read()
        m = re.search(r"<StopMode>([^<]*)", t)
        if m:
            stop = {"FadeOut": "fade", "StopInstantly": "sofort",
                    "StopAtEndOfLoop": "ende"}.get(m.group(1), m.group(1))
        m = re.search(r"<RhythmType>([^<]*)", t)
        if m:
            rhy = {"Silence": "—", "StudioKickAndHighHat": "klick"}.get(m.group(1), m.group(1))
    except OSError:
        pass
    try:
        t = open(ph, encoding="utf-8", errors="replace").read()
        m = re.search(r"<IsLoop>([^<]*)", t)
        if m:
            loop = "loop" if m.group(1) == "1" else "single"
    except OSError:
        pass
    return stop, rhy, loop


def wav_info(pfad):
    """Dauer, Rate, Kanäle, Bittiefe.

    Erst mit dem eingebauten wave-Modul (geht ohne ffmpeg, also auch auf
    einem Mac ohne Homebrew). Nur wenn das scheitert, wird ffprobe gefragt.
    """
    try:
        import wave as _wave
        with _wave.open(pfad, "rb") as w:
            rate = w.getframerate()
            kanaele = w.getnchannels()
            bits = w.getsampwidth() * 8
            dauer = w.getnframes() / float(rate) if rate else 0.0
        return (dauer, rate, kanaele, bits)
    except Exception:
        pass
    werte = {}
    if werkzeug_pfad("ffprobe"):         # ohne ffmpeg gibt es ffprobe nicht
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=sample_rate,channels,bits_per_sample:format=duration",
                            "-of", "default=noprint_wrappers=1", pfad],
                           capture_output=True, text=True)
        for zeile in r.stdout.splitlines():
            if "=" in zeile:
                k, v = zeile.split("=", 1)
                werte[k] = v
    try:
        dauer = float(werte.get("duration", 0))
        rate = int(werte.get("sample_rate", 0))
        kanaele = int(werte.get("channels", 0))
        bits = int(werte.get("bits_per_sample", 0) or 0)
        if dauer > 0:
            return (dauer, rate, kanaele, bits)
    except ValueError:
        pass

    # Ohne ffprobe (z. B. Mac ohne Homebrew): afinfo fragen.
    if MAC and shutil.which("afinfo"):
        r = subprocess.run(["afinfo", pfad], capture_output=True, text=True)
        dauer = rate = kanaele = 0
        m = re.search(r"estimated duration: ([0-9.]+) sec", r.stdout)
        if m:
            dauer = float(m.group(1))
        m = re.search(r"Data format:\s*\d+ ch, (\d+) Hz", r.stdout)
        if m:
            kanaele = int(m.group(1)); rate = int(m.group(2))
        m = re.search(r"(\d+)-bit", r.stdout)
        bits = int(m.group(1)) if m else 0
        return (dauer, rate, kanaele, bits)
    return (0.0, 0, 0, 0)


def phrase_wav(patch_pfad):
    q = os.path.join(patch_pfad, "PhraseA", "phrase.wav")
    return q if os.path.exists(q) else None


def sekunden(s):
    return "%d:%04.1f" % (int(s) // 60, s % 60)


# ------------------------------------------------------------------ Befehle


def saeubere_karte(basis):
    """Entfernt ._* und .DS_Store. macOS legt sie beim Kopieren an;
    der JamMan kann daran scheitern."""
    entfernt = 0
    for w, d, ds in os.walk(os.path.dirname(basis)):
        for x in list(ds):
            if x.startswith("._") or x == ".DS_Store":
                try:
                    os.remove(os.path.join(w, x))
                    entfernt += 1
                except OSError:
                    pass
    if entfernt:
        print(T("  %d Apple-Dateien entfernt (._* und .DS_Store).") % entfernt)


def befehl_liste(basis):
    alle = patches(basis)
    if not alle:
        print(T("  Keine Patches auf der Karte."))
        return
    print(T("  %-10s %-34s %8s  %s") % ("Ordner", "Name", "Dauer", "WAV-Format"))
    for nummer, name, pfad in alle:
        w = phrase_wav(pfad)
        if w:
            dauer, rate, kanaele, bits = wav_info(w)
            format_text = "%d Hz / %d Bit / %d K" % (rate, bits, kanaele)
            ok = (rate, bits, kanaele) == WOLL
            print(T("  %-10s %-34s %8s  %s%s%s") % (
                name, patch_name(pfad)[:34], sekunden(dauer), format_text,
                "" if ok else "   ← abweichend",
                "   ← über 10 min, Pedal nimmt sie nicht an"
                if dauer > MAX_LOOP_SEKUNDEN else ""))
        else:
            print(T("  %-10s %-34s %8s  %s") % (name, patch_name(pfad)[:34], "—", "keine WAV"))
    print(T("\n  %d Patches, %s belegt.") % (len(alle),
          sum(1 for _, _, p in alle if phrase_wav(p))))


EINSTELLUNG_DATEI = "~/.jamman-werkzeug.cnf"


def einstellung_lesen(name, vorgabe=""):
    """Kleine Einstellungsdatei im Benutzerordner (geht auf allen Systemen)."""
    pfad = os.path.expanduser(EINSTELLUNG_DATEI)
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                if "=" in zeile:
                    schluessel, wert = zeile.split("=", 1)
                    if schluessel.strip() == name:
                        return wert.strip()
    except OSError:
        pass
    return vorgabe


def einstellung_schreiben(name, wert):
    pfad = os.path.expanduser(EINSTELLUNG_DATEI)
    werte = {}
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                if "=" in zeile:
                    schluessel, alt = zeile.split("=", 1)
                    werte[schluessel.strip()] = alt.strip()
    except OSError:
        pass
    werte[name] = str(wert)
    with open(pfad, "w", encoding="utf-8") as f:
        for schluessel in sorted(werte):
            f.write("%s=%s\n" % (schluessel, werte[schluessel]))


def karten_stand(basis):
    """Kennzahl der Karte: Anzahl Patches, Summe der Größen, jüngste Änderung.

    Damit lässt sich erkennen, ob sich seit der letzten Sicherung etwas
    geändert hat - dann muss nicht bei jedem Start kopiert werden.
    """
    anzahl = 0
    groesse = 0
    juengste = 0.0
    for wurzel, ordner, dateien in os.walk(basis):
        for datei in dateien:
            if datei.startswith("._") or datei == ".DS_Store":
                continue
            pfad = os.path.join(wurzel, datei)
            try:
                st = os.stat(pfad)
            except OSError:
                continue
            anzahl += 1
            groesse += st.st_size
            juengste = max(juengste, st.st_mtime)
    return "%d-%d-%d" % (anzahl, groesse, int(juengste))


def beim_start_sichern(basis, behalten=3, still=False):
    """Sichert die Karte beim Start - aber nur, wenn sie sich geändert hat.

    Anlass 10.11.2026: Die Karte hatte nach einem Schreibvorgang ihre
    Einträge verloren; nur die Sicherung hat sie gerettet. Deshalb auf
    Wunsch eine Sicherung beim Start, ohne dass bei jedem Start 1,2 GB
    kopiert werden.
    """
    if not basis:
        return None
    try:
        basis = karte_pruefen(basis)      # immer der Ordner JamManSoloXT
    except SystemExit:
        return None
    stand = karten_stand(basis)
    vorher = einstellung_lesen("letzter_stand")
    if stand == vorher:
        if not still:
            print(T("  Sicherung beim Start: Karte unverändert, nichts zu tun."))
        return None
    ziel = os.path.expanduser(SICHERUNG_WURZEL)
    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ordner = os.path.join(ziel, "Start_" + stempel)
    os.makedirs(ordner, exist_ok=True)
    print(T("  Sicherung beim Start: %s") % ordner)
    shutil.copytree(basis, os.path.join(ordner, UNTERORDNER), dirs_exist_ok=True)
    anzahl = len(patches(basis))
    groesse = sum(os.path.getsize(os.path.join(w, x))
                  for w, d, ds in os.walk(ordner) for x in ds)
    print(T("    gesichert: %d Patches, %.0f MB") % (anzahl, groesse / 1024 / 1024))
    einstellung_schreiben("letzter_stand", stand)
    # Alte Start-Sicherungen aufräumen (nur die eigenen)
    alte = sorted(d for d in os.listdir(ziel) if d.startswith("Start_"))
    for d in alte[:-behalten] if len(alte) > behalten else []:
        try:
            shutil.rmtree(os.path.join(ziel, d))
            print(T("    alte Sicherung entfernt: %s") % d)
        except OSError:
            pass
    return ordner


def befehl_sichern(basis, ziel, schreiben):
    ziel = os.path.abspath(os.path.expanduser(ziel))
    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M")
    zielordner = os.path.join(ziel, "JamManSicherung_" + stempel)
    print(T("  Quelle: %s") % basis)
    print(T("  Ziel:   %s") % zielordner)
    if not schreiben:
        print(T("  Trockenlauf — nichts kopiert. Mit --schreiben ausführen."))
        return
    os.makedirs(zielordner, exist_ok=True)
    shutil.copytree(basis, os.path.join(zielordner, UNTERORDNER), dirs_exist_ok=True)
    anzahl = len(patches(basis))
    groesse = sum(os.path.getsize(os.path.join(w, x))
                  for w, d, ds in os.walk(zielordner) for x in ds)
    print(T("  Gesichert: %d Patches, %.0f MB.") % (anzahl, groesse / 1024 / 1024))


def naechste_nummer(basis):
    """Erste freie Platznummer, im Format der vorhandenen Namen.

    Am 26.10.2026 wurde hier die hoechste Nummer plus eins genommen und die
    Breite aus dem laengsten Namen. Weil ein Patch "Patch200" heisst, bekam
    ein neuer Patch die Nummer 201 statt der freien 25 - und stand damit
    nicht dort, wo der Nutzer ihn erwartet.
    """
    from collections import Counter
    alle = patches(basis)
    benutzt = set(n for n, _, _ in alle)
    breiten = Counter(len(name) - 5 for _, name, _ in alle)
    breite = breiten.most_common(1)[0][0] if breiten else 2
    frei = 1
    while frei in benutzt:
        frei += 1
    return "%0*d" % (breite, frei)


def vorlage_finden(basis):
    """Ein vollständiger Patch dient als Vorlage für patch.xml und phrase.xml."""
    for nummer, name, pfad in patches(basis):
        if os.path.exists(os.path.join(pfad, "patch.xml")) and \
           os.path.exists(os.path.join(pfad, "PhraseA", "phrase.xml")):
            return pfad
    return None


def setze_wert(text, tag, wert):
    """Ersetzt <tag>…</tag>. Fehlt das Feld in der Vorlage, wird es
    eingefügt - sonst bliebe die gewünschte Einstellung wirkungslos."""
    muster = r"<%s>.*?</%s>" % (tag, tag)
    if re.search(muster, text, flags=re.S):
        return re.sub(muster, "<%s>%s</%s>" % (tag, wert, tag),
                      text, count=1, flags=re.S), True
    neu = re.sub(r"</JamManPatch>",
                 "  <%s>%s</%s>\n</JamManPatch>" % (tag, wert, tag),
                 text, count=1)
    return neu, False


def wellenform(pfad, breite=900):
    """Hüllkurve für die Anzeige: je Bildspalte der größte Ausschlag.

    Liefert eine Liste von Werten zwischen 0 und 1, dazu die Dauer und
    den Spitzenwert der Datei.

    Wichtig für das Tempo: max() und min() auf einem array-Abschnitt
    laufen in C. Die erste Fassung baute eine Python-Liste aller
    Einzelwerte und brauchte für 75 MB über zwei Sekunden - das Fenster
    stand so lange (am 10.11.2026 als Trägheit mit Beachball bemerkt).
    """
    import array
    with wave.open(pfad, "rb") as w:
        kanaele, breite_daten, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        gesamt = w.getnframes()
        if not gesamt or breite_daten != 2:
            return [], 0.0, 0
        dauer = gesamt / float(rate)
        # Stichproben statt der ganzen Datei: je Bildspalte ein kurzes
        # Fenster lesen. 900 Sprünge in einer unkomprimierten WAV sind
        # billiger als 150 MB am Stück zu lesen und zu durchsuchen.
        if gesamt > 500000:
            fenster = max(kanaele, int(rate * 0.02) * kanaele)   # 20 ms
            werte = []
            spitze = 1
            for i in range(breite):
                pos = int(gesamt * i / float(breite))
                w.setpos(min(pos, max(0, gesamt - 1)))
                roh = w.readframes(min(fenster // kanaele, gesamt - w.tell()))
                if not roh:
                    werte.append(0.0)
                    continue
                a = array.array("h"); a.frombytes(roh)
                m = max(max(a), -min(a))
                spitze = max(spitze, m)
                werte.append(m)
            return [x / float(spitze) for x in werte], dauer, spitze

        roh = w.readframes(gesamt)
    a = array.array("h"); a.frombytes(roh)
    if not len(a):
        return [], 0.0, 0
    pro_spalte = max(1, len(a) // breite)
    spitze = max(max(a), -min(a)) or 1
    werte = []
    for i in range(0, len(a) - pro_spalte, pro_spalte):
        teil = a[i:i + pro_spalte]
        werte.append(max(max(teil), -min(teil)) / float(spitze))
    return werte, dauer, spitze


def verbinde_patches(pfade, ziel):
    """Hängt mehrere Aufnahmen lückenlos aneinander.

    Notwendig für nahtloses Weiterspielen: startet man für jeden Patch
    einen eigenen Spieler, entsteht dazwischen eine Pause (Prozessstart
    und Abfrage-Rhythmus). Eine einzige Datei hat keine Naht.
    Gibt die Dauer jedes Teils zurück.
    """
    import array
    dauern = []
    daten = []
    kanaele = rate = None
    for pfad in pfade:
        with wave.open(pfad, "rb") as w:
            k, b, rt = w.getnchannels(), w.getsampwidth(), w.getframerate()
            if b != 2 or k != 2 or rt != 44100:
                return None, []
            if kanaele is None:
                kanaele, rate = k, rt
            roh = w.readframes(w.getnframes())
        a = array.array("h"); a.frombytes(roh)
        daten.append(a)
        dauern.append(len(a) / float(kanaele * rate))
    gesamt = array.array("h")
    for teil in daten:
        gesamt.extend(teil)
    with open(ziel, "wb") as f:
        inhalt = gesamt.tobytes()
        kopf = b"RIFF" + struct.pack("<I", 36 + len(inhalt)) + b"WAVE"
        kopf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, rate,
                                     rate * 4, 4, 16)
        kopf += b"data" + struct.pack("<I", len(inhalt))
        f.write(kopf); f.write(inhalt)
    return dauern, daten


def abspielen_ab(pfad, von=0.0, bis=None):
    """Spielt eine Aufnahme ab einem Zeitpunkt.

    afplay kann nicht springen, deshalb wird bei Bedarf ein Ausschnitt in
    eine Zwischendatei geschrieben und die abgespielt. Gibt
    (Prozess, Zwischendatei oder None, Startzeit) zurück.
    """
    von = max(0.0, float(von or 0))
    if von <= 0.01 and bis is None:
        return abspielen_datei(pfad), None, 0.0
    dauer = wav_info(pfad)[0]
    bis = dauer if bis is None else min(bis, dauer)
    zwischer = neue_tempdatei("jamman-ab-", ".wav")
    if not schneide_bereich(pfad, zwischer, von, bis):
        os.unlink(zwischer)
        return abspielen_datei(pfad), None, 0.0
    return abspielen_datei(zwischer), zwischer, von


def finde_schnittpunkte(pfad, anzahl, fenster=90.0, min_pause=0.25):
    """Sucht musikalisch brauchbare Schnittstellen.

    Vorgehen: Hüllkurve in 50-ms-Schritten, mittlerer Pegel bestimmen,
    dann zusammenhängende ruhige Stellen (unter 35 % des Mittels) suchen.
    Geschnitten wird am ENDE einer solchen Pause - dort beginnt der
    nächste musikalische Abschnitt.

    Rückgabe: Liste von Zeitpunkten (Sekunden) und eine Liste der
    Begründungen. Werden nicht genug Pausen gefunden, wird der Zeitpunkt
    rechnerisch gewählt und das gesagt.
    """
    with wave.open(pfad, 'rb') as w:
        kanaele, breite, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        roh = w.readframes(w.getnframes())
    import array
    a = array.array("h"); a.frombytes(roh)
    if breite != 2:
        return [], ["Aufnahme ist kein 16-Bit-WAV"]
    if kanaele == 2:
        mono = [(a[i] + a[i+1]) / 2.0 for i in range(0, len(a) - 1, 2)]
    else:
        mono = [float(x) for x in a]
    dauer = len(mono) / float(rate)

    schritt, laenge = 0.05, 0.10
    h = []
    i = 0
    while i * schritt + laenge < dauer:
        von = int(i * schritt * rate)
        bis = int((i * schritt + laenge) * rate)
        teil = mono[von:bis]
        h.append((i * schritt, math.sqrt(sum(x*x for x in teil) / len(teil))))
        i += 1
    if not h:
        return [], ["Aufnahme zu kurz"]
    peg = sorted(v for _, v in h)
    mittel = peg[len(peg)//2]
    ruhig = [(tm, v) for tm, v in h if v < mittel * 0.35]

    # Gruppen ruhiger Stellen bilden
    gruppen = []
    for tm, v in ruhig:
        if gruppen and tm - gruppen[-1][-1][0] <= schritt * 3:
            gruppen[-1].append((tm, v))
        else:
            gruppen.append([(tm, v)])
    pausen = [(g[0][0], g[-1][0] + laenge) for g in gruppen
              if (g[-1][0] + laenge) - g[0][0] >= min_pause]

    punkte, gruende, paare = [], [], []
    for teil in range(1, anzahl):
        ziel = dauer * teil / float(anzahl)
        nahe = [(abs(((von + bis) / 2.0) - ziel), bis) for von, bis in pausen
                if abs(((von + bis) / 2.0) - ziel) <= fenster]
        if nahe:
            nahe.sort()
            punkte.append(nahe[0][1])
            paare.append(next((p for p in pausen if abs(p[1] - nahe[0][1]) < 1e-6), None))
            gruende.append("Ende einer Pause, %.1f s vom rechnerischen Punkt entfernt"
                           % nahe[0][0])
        else:
            punkte.append(ziel)
            paare.append(None)
            gruende.append("keine Pause in der Nähe - rechnerisch geschnitten")
    return punkte, gruende, paare


def schneide_bereich(quelle, ziel, von_sekunden, bis_sekunden):
    """Kopiert einen Zeitbereich einer WAV-Datei in eine neue Datei.

    Sample-genau: es wird nichts hinzugefügt und nichts weggelassen.
    """
    angaben = wav_kopf(quelle)
    if not angaben or angaben.get("format") is None:
        return False
    with open(quelle, "rb") as f:
        f.seek(12)
        fmt = None
        start = laenge = None
        while True:
            k = f.read(8)
            if len(k) < 8:
                break
            name, groesse = k[0:4], struct.unpack("<I", k[4:8])[0]
            if name == b"fmt ":
                fmt = f.read(groesse)
            elif name == b"data":
                start, laenge = f.tell(), groesse
                break
            else:
                f.seek(groesse + (groesse % 2), 1)
        if fmt is None or start is None:
            return False
        format_, kanaele, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[0:16])
        if format_ not in (1, 0xFFFE) or bits != 16:
            return False
        anfang = int(von_sekunden * rate) * block
        ende = int(bis_sekunden * rate) * block
        anfang -= anfang % block
        ende -= ende % block
        ende = min(ende, laenge)
        if ende <= anfang:
            return False
        f.seek(start + anfang)
        daten = f.read(ende - anfang)
    with open(ziel, "wb") as z:
        kopf = b"RIFF" + struct.pack("<I", 36 + len(daten)) + b"WAVE"
        kopf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, kanaele, rate,
                                     rate * block, block, 16)
        kopf += b"data" + struct.pack("<I", len(daten))
        z.write(kopf); z.write(daten)
    return True


def befehl_teilen(basis, wav, name, schreiben, teile=None, laenge=None,
                  wurzel=None, stopp=None, wiedergabe=None, sinnvoll=True,
                  ohne_pause=True):
    """Eine Datei in mehrere Patches aufteilen.

    Gedacht für Aufnahmen über 10 Minuten, die das Pedal sonst nicht
    annimmt. Die Teile liegen sample-genau aneinander - kein Ton fehlt,
    keiner doppelt.
    """
    dauer, rate, kanaele, bits = wav_info(wav)
    if dauer <= 0:
        raise SystemExit("Fehler: '%s' ist keine lesbare Audiodatei." % wav)
    if laenge:
        anzahl = max(1, int(math.ceil(dauer / float(laenge))))
        stueck = float(laenge)
    elif teile:
        anzahl = max(1, int(teile))
        stueck = dauer / float(anzahl)
    else:
        raise SystemExit("Fehler: --teile N oder --laenge SEKUNDEN angeben.")
    if stueck > MAX_LOOP_SEKUNDEN:
        raise SystemExit("Fehler: %.1f Minuten pro Teil sind zu lang "
                         "(Grenze %d Minuten)." % (stueck / 60.0, MAX_LOOP_SEKUNDEN //
                                                   60))
    print(T("  Datei:  %s") % os.path.basename(wav))
    print(T("  Dauer:  %s") % sekunden(dauer))
    print(T("  Teile:  %d × etwa %s") % (anzahl, sekunden(stueck)))
    # Zuerst auf 44,1 kHz/16 Bit bringen: die Analyse und das Schneiden
    # arbeiten mit 16-Bit-Daten. Eine 24-Bit-WAV hat hier sonst nichts zu
    # suchen (am 10.11.2026 mit IndexError abgestürzt).
    zwischer = tempfile.mkdtemp(prefix="jamman-teile-")
    ganz = os.path.join(zwischer, "ganz.wav")
    if not wandle_nach_44k(wav, ganz):
        shutil.rmtree(zwischer, ignore_errors=True)
        raise SystemExit("Fehler: Datei ließ sich nicht umwandeln.")
    dauer = wav_info(ganz)[0]

    mitteilen = []
    if sinnvoll and anzahl > 1:
        print(T("  Suche musikalisch brauchbare Schnittstellen …"))
        punkte, gruende, paare = finde_schnittpunkte(ganz, anzahl,
                                                     fenster=max(90.0, stueck/4))
        if len(punkte) != anzahl - 1:
            print(T("  Hinweis: keine brauchbaren Pausen gefunden (%s) -")
                  % (gruende[0] if gruende else "kein Ergebnis"))
            print(T("           es wird gleichmäßig geschnitten."))
            punkte = [dauer * k / float(anzahl) for k in range(1, anzahl)]
            gruende = ["rechnerisch"] * len(punkte)
            paare = [None] * len(punkte)
        grenzen = [0.0] + punkte + [dauer]
        if ohne_pause:
            # Jeder Teil endet dort, wo der Ton aufhört, und beginnt dort,
            # wo er nach der Pause wieder einsetzt. Die Stille fällt weg.
            mitteilen = []
            for i in range(anzahl):
                von = 0.0 if i == 0 else (
                    paare[i-1][1] if paare[i-1] else punkte[i-1])
                bis = dauer if i == anzahl - 1 else (
                    paare[i][0] if paare[i] else punkte[i])
                mitteilen.append((von, bis))
            print(T("  Pause an der Naht wird herausgenommen"))
        else:
            for i in range(anzahl):
                mitteilen.append((grenzen[i], min(dauer, grenzen[i+1])))
        for i, (von, bis) in enumerate(mitteilen):
            print(T("    Teil %d: %7.1f – %7.1f s  (%s)") % (
                i + 1, von, bis, sekunden(bis - von)))
        for i, (pu, gr) in enumerate(zip(punkte, gruende), start=1):
            print(T("      Schnitt %d bei %s: %s") % (i, sekunden(pu), T(gr)))
        mitteilen = [(max(0.0, v), min(dauer, b)) for v, b in mitteilen]
        # Sicherheit: kein Teil über der Grenze
        for i, (von, bis) in enumerate(mitteilen, start=1):
            if bis - von > MAX_LOOP_SEKUNDEN:
                print(T("  Hinweis: Teil %d wäre %.1f min - er wird auf 9:59 gekürzt.")
                      % (i, (bis - von) / 60.0))
    else:
        for i in range(anzahl):
            von = i * stueck
            bis = min(dauer, (i + 1) * stueck)
            mitteilen.append((von, bis))
            print(T("    Teil %d: %s – %s") % (i + 1, sekunden(von), sekunden(bis)))
    if not schreiben:
        print(T("  Trockenlauf — nichts angelegt. Mit --schreiben ausführen."))
        return

    try:
        for i, (von, bis) in enumerate(mitteilen, start=1):
            teil = os.path.join(zwischer, "teil%d.wav" % i)
            if not schneide_bereich(ganz, teil, von, bis):
                raise SystemExit("Fehler: Teil %d ließ sich nicht schneiden." % i)
            befehl_aufspielen(basis, teil, "%s %d/%d" % (name, i, anzahl),
                              True, stopp, None, wiedergabe)
    finally:
        shutil.rmtree(zwischer, ignore_errors=True)


def befehl_aufspielen(basis, wav, name, schreiben, stopp=None, rhythmus=None,
                      wiedergabe=None, fade=None, gain=None, normieren=False,
                      begrenzen=True, trotzdem=False, kuerzen=False,
                      laenge=None):
    wav = os.path.abspath(os.path.expanduser(wav))
    if not os.path.exists(wav):
        raise SystemExit("Fehler: '%s' gibt es nicht." % wav)
    dauer, rate, kanaele, bits = wav_info(wav)
    if dauer <= 0:
        raise SystemExit("Fehler: '%s' ist keine lesbare Audiodatei.%s"
                         % (wav, unlesbar_hinweis(wav)))
    ziel_laenge = None
    if dauer > MAX_LOOP_SEKUNDEN:
        if laenge:
            ziel_laenge = min(laenge, float(MAX_LOOP_SEKUNDEN))
        elif kuerzen:
            ziel_laenge = float(MAX_LOOP_SEKUNDEN) - 1.0     # 9:59, mit Luft
        elif not trotzdem:
            raise SystemExit(
                "Fehler: Die Datei ist %.1f Minuten lang.\n"
                "  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n"
                "  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n"
                "  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n"
                "  nicht erkannt - und können dabei verschwinden.\n\n"
                "  So geht es weiter:\n"
                "    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n"
                "                         12 ms ausgeblendet, damit es nicht knackt)\n"
                "    --laenge 480         auf 8:00 kürzen\n"
                "    --trotzdem           unverändert schreiben (zum Ausprobieren)"
                % (dauer / 60.0, MAX_LOOP_SEKUNDEN // 60))

    passt = (rate, kanaele, bits) == WOLL
    name = name or os.path.splitext(os.path.basename(wav))[0]
    zielnummer = naechste_nummer(basis)
    zielpfad = os.path.join(basis, "Patch" + zielnummer)
    vorlage = vorlage_finden(basis)

    print(T("  Quelle:       %s") % os.path.basename(wav))
    print(T("  Dauer:        %s") % sekunden(dauer))
    print(T("  Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s") %
          (rate, bits, kanaele, "" if passt else T("   ← wird umgewandelt")))
    print(T("  Stopp:        %s") % (T("wie Vorlage") if not stopp else
          "%s (%s)" % (stopp, STOPP.get(stopp, stopp))))
    print(T("  Rhythmus:     %s") % (T("wie Vorlage") if not rhythmus else
          "%s (%s)" % (rhythmus, RHYTHMUS.get(rhythmus, (rhythmus,))[0])))
    print(T("  Wiedergabe:   %s") % (T("wie Vorlage") if not wiedergabe else
          "%s (IsLoop %s)" % (wiedergabe, WIEDERGABE.get(wiedergabe, "?"))))
    if fade:
        print(T("  Fade:         letzte %.1f s werden ausgeblendet") % fade)
    if normieren:
        print(T("  Pegel:        auf -1 dB Spitze normieren"))
    elif gain:
        print(T("  Pegel:        %+.1f dB") % gain)
    print(T("  Neuer Patch:  Patch%s  (Name: %s)") % (zielnummer, name))
    print(T("  Vorlage:      %s") % (os.path.basename(vorlage) if vorlage else "KEINE — Abbruch"))
    if not vorlage:
        raise SystemExit("Fehler: kein vollständiger Patch als Vorlage gefunden.")
    if os.path.exists(zielpfad):
        raise SystemExit("Fehler: '%s' liegt schon vor." % zielpfad)
    if not schreiben:
        print(T("  Trockenlauf — nichts geschrieben. Mit --schreiben ausführen."))
        return

    os.makedirs(os.path.join(zielpfad, "PhraseA"))

    # 1) WAV ablegen — bei Bedarf vorher umwandeln
    ziel_wav = os.path.join(zielpfad, "PhraseA", "phrase.wav")
    if passt:
        shutil.copy2(wav, ziel_wav)
    else:
        if not wandle_nach_44k(wav, ziel_wav):
            raise SystemExit(
                "Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n"
                "  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n"
                "  ffmpeg installieren." % (rate, kanaele))

    if ziel_laenge:
        if kuerze_wav(ziel_wav, ziel_laenge):
            print(T("  Kürzen:       auf %s geschnitten (harter Schnitt, "
                  "12 ms ausgeblendet)") % sekunden(ziel_laenge))
        else:
            raise SystemExit("Fehler: Die Datei ließ sich nicht kürzen.")

    # Kopf auf einfaches PCM umschreiben - das Pedal lehnt manche
    # WAVE_FORMAT_EXTENSIBLE-Dateien ab (aus M4A entstehend).
    if normalisiere_wav(ziel_wav):
        kopf = wav_kopf(ziel_wav)
        print(T("  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben"))
        if not kopf or kopf.get("format") != 1 or kopf.get("fmt_groesse") != 16:
            print(T("  Achtung: der WAV-Kopf ist weiterhin ungewöhnlich."))

    spitze, _ = pegel_info(ziel_wav)
    if spitze is None or spitze < -90:
        shutil.rmtree(zielpfad, ignore_errors=True)
        raise SystemExit("Fehler: Die Umwandlung hat keine hörbare Aufnahme\n"
                         "  ergeben (nur Stille). Datei prüfen: %s" % os.path.basename(wav))

    if fade:
        if not fade_ausblenden(ziel_wav, fade):
            print(T("  Hinweis: Fade konnte nicht eingerechnet werden."))
    if normieren or gain:
        ergebnis = (pegel_anpassen(ziel_wav, ziel_spitze_db=-1.0) if normieren
                    else pegel_anpassen(ziel_wav, db=gain, begrenzen=begrenzen))
        if ergebnis:
            print_pegel(ergebnis)
            if ergebnis.get("nachher") is not None:
                schreibe_pegelprotokoll(basis, [{
                    "ordner": os.path.basename(pfad), "name": patch_name(pfad),
                    "faktor": 10 ** ((ergebnis["nachher"] - ergebnis["vorher"]) / 20.0),
                    "vorher": ergebnis["vorher"], "nachher": ergebnis["nachher"]}],
                    "pegel", wurzel)
        else:
            print(T("  Hinweis: Pegel ließ sich nicht ändern (kein 16-Bit-WAV?)."))

    # 2) phrase.xml aus der Vorlage übernehmen, IsLoop bei Bedarf setzen
    quelle_phrase = os.path.join(vorlage, "PhraseA", "phrase.xml")
    phrase_text = open(quelle_phrase, encoding="utf-8").read()
    if wiedergabe and wiedergabe in WIEDERGABE:
        phrase_text, vorhanden = setze_wert(phrase_text, "IsLoop", WIEDERGABE[wiedergabe])
        if not vorhanden:
            print(T("  Hinweis: Die Vorlage hatte kein Feld IsLoop - es wurde ergänzt."))
    open(os.path.join(zielpfad, "PhraseA", "phrase.xml"), "w", encoding="utf-8").write(phrase_text)

    # 3) patch.xml aus der Vorlage, Name und Kennung neu
    quelle_xml = os.path.join(vorlage, "patch.xml")
    text = open(quelle_xml, encoding="utf-8").read()
    text = re.sub(r"<PatchName>.*?</PatchName>",
                  "<PatchName>%s</PatchName>" % name.replace("&", "&amp;").replace("<", "&lt;"),
                  text, count=1, flags=re.S)
    text = re.sub(r"<ID>.*?</ID>", "<ID>%s</ID>" % uuid.uuid4(), text, count=1, flags=re.S)
    if stopp and stopp in STOPP:
        text, vorhanden = setze_wert(text, "StopMode", STOPP[stopp])
        if not vorhanden:
            print(T("  Hinweis: Die Vorlage hatte kein Feld StopMode - es wurde ergänzt."))
    if rhythmus and rhythmus in RHYTHMUS:
        wert, version = RHYTHMUS[rhythmus]
        text, vorhanden = setze_wert(text, "RhythmType", wert)
        if not vorhanden:
            print(T("  Hinweis: Die Vorlage hatte kein Feld RhythmType - es wurde ergänzt."))
        # SettingsVersion gehoert zum Rhythmus (0 bei Silence, 1 beim Klick)
        text, _ = setze_wert(text, "SettingsVersion", version)
    open(os.path.join(zielpfad, "patch.xml"), "w", encoding="utf-8").write(text)

    saeubere_karte(basis)
    print(T("  Fertig: %s angelegt.") % zielpfad)
    print(T("  Karte auswerfen, in den JamMan stecken — der Patch erscheint als Patch%s.") % zielnummer)


def sicherung_der_einstellungen(basis, pfad, grund, wurzel=None):
    """Sichert nur patch.xml und phrase.xml - kein WAV.

    Beim Ändern von Name oder Einstellungen bleibt die Aufnahme unberührt;
    eine Kopie der ganzen Patch-Mappe (bis zu 90 MB) wäre dafür unnötig.
    """
    ordner = os.path.expanduser(wurzel or SICHERUNG_WURZEL)
    ziel = freier_name(os.path.join(ordner, "Einstellungen_%s_%s" % (
        datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), grund), os.path.basename(pfad)))
    try:
        os.makedirs(os.path.join(ziel, "PhraseA"), exist_ok=True)
        for name in ("patch.xml", os.path.join("PhraseA", "phrase.xml")):
            quelle = os.path.join(pfad, name)
            if os.path.exists(quelle):
                shutil.copy2(quelle, os.path.join(ziel, name))
    except OSError:
        raise SystemExit("Fehler: '%s' lässt sich nicht schreiben.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>" % ordner)
    return os.path.dirname(ziel)


def befehl_aendern(basis, nummer, schreiben, stopp=None, rhythmus=None,
                   wiedergabe=None, name=None, wurzel=None, fade=None,
                   gain=None, normieren=False, begrenzen=True):
    """Einstellungen eines vorhandenen Patches ändern."""
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit("Fehler: Patch%d gibt es nicht." % nummer)
    pfad = alle[nummer]
    print(T("  Patch:        %s  (%s)") % (os.path.basename(pfad), patch_name(pfad)))
    if not (stopp or rhythmus or wiedergabe or name or fade or gain or normieren):
        raise SystemExit("Fehler: nichts angegeben (--stopp, --rhythmus, "
                         "--wiedergabe, --name oder --fade).")
    if stopp:      print(T("  Stopp:        %s (%s)") % (stopp, STOPP[stopp]))
    if rhythmus:   print(T("  Rhythmus:     %s (%s)") % (rhythmus, RHYTHMUS[rhythmus][0]))
    if wiedergabe: print(T("  Wiedergabe:   %s (IsLoop %s)") % (wiedergabe, WIEDERGABE[wiedergabe]))
    if name:       print(T("  Name:         %s") % name)
    if fade:
        print(T("  Fade:         letzte %.1f s der Aufnahme ausblenden") % fade)
    if normieren:
        print(T("  Pegel:        auf -1 dB Spitze normieren"))
    elif gain:
        print(T("  Pegel:        %+.1f dB") % gain)
    if fade or gain or normieren:
        print(T("                (verändert die Audiodatei; vorher wird gesichert)"))
    if not schreiben:
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return

    if fade or gain or normieren:
        ziel = sicher_ablegen(basis, [pfad], "vor_pegel", wurzel)
    else:
        ziel = sicherung_der_einstellungen(basis, pfad, "vor_aendern", wurzel)
    px = os.path.join(pfad, "patch.xml")
    ph = os.path.join(pfad, "PhraseA", "phrase.xml")
    text = open(px, encoding="utf-8").read()
    if stopp:
        text, _ = setze_wert(text, "StopMode", STOPP[stopp])
    if rhythmus:
        wert, version = RHYTHMUS[rhythmus]
        text, _ = setze_wert(text, "RhythmType", wert)
        text, _ = setze_wert(text, "SettingsVersion", version)
    if name:
        text, _ = setze_wert(text, "PatchName", name.replace("&", "&amp;").replace("<", "&lt;"))
    open(px, "w", encoding="utf-8").write(text)

    if wiedergabe and os.path.exists(ph):
        ptext = open(ph, encoding="utf-8").read()
        ptext, _ = setze_wert(ptext, "IsLoop", WIEDERGABE[wiedergabe])
        open(ph, "w", encoding="utf-8").write(ptext)

    if fade or gain or normieren:
        wav = os.path.join(pfad, "PhraseA", "phrase.wav")
        if not os.path.exists(wav):
            raise SystemExit("Fehler: Patch%d hat keine phrase.wav." % nummer)
        if normalisiere_wav(wav):
            print(T("  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben"))
        if fade and not fade_ausblenden(wav, fade):
            raise SystemExit("Fehler: Die Aufnahme ließ sich nicht ausblenden "
                             "(kein 16-Bit-WAV?).")
        if gain or normieren:
            ergebnis = (pegel_anpassen(wav, ziel_spitze_db=-1.0) if normieren
                        else pegel_anpassen(wav, db=gain, begrenzen=begrenzen))
            if not ergebnis:
                raise SystemExit("Fehler: Der Pegel ließ sich nicht ändern.")
            print_pegel(ergebnis)

    saeubere_karte(basis)
    print(T("  Geändert. Sicherung: %s") % ziel)


def befehl_auswerfen(karte):
    """Karte auswerfen. Vorher werden die Apple-Dateien entfernt."""
    ziel = volumen_pfad(karte)
    if not os.path.isdir(ziel):
        raise SystemExit("Fehler: '%s' ist nicht eingelegt." % karte)
    try:
        saeubere_karte(os.path.join(ziel, UNTERORDNER))
    except Exception:
        pass
    erfolg, meldung = auswerfen_datentraeger(karte)
    if erfolg:
        print(T("  %s Sie kann jetzt abgezogen werden.") % meldung)
    else:
        raise SystemExit("Fehler beim Auswerfen: %s" % meldung)


def namensform_pruefen(basis):
    """Welche Patch-Ordner heißen nicht so, wie das Gerät sie schreibt?

    FAT32 speichert Namen mitunter in Großbuchstaben (PATCH01). Das Pedal
    liest ohne Rücksicht darauf, andere Programme aber nicht. Diese
    Funktion sagt, was zu ändern wäre - sie ändert nichts.
    """
    from collections import Counter
    alle = patches(basis)
    if not alle:
        return [], 2
    breiten = Counter()
    for nummer, name, _ in alle:
        ziffern = re.sub(r"\D", "", name)
        breiten[len(ziffern)] += 1
    breite = breiten.most_common(1)[0][0]
    aenderungen = []
    for nummer, name, pfad in alle:
        soll = "Patch%0*d" % (breite, nummer)
        if name != soll:
            aenderungen.append((name, soll, pfad))
    return aenderungen, breite


def befehl_namensform(basis, schreiben, wurzel=None):
    """Patch-Ordner auf die Schreibweise des Geräts bringen (Patch01)."""
    aenderungen, breite = namensform_pruefen(basis)
    print(T("  Schreibweise des Geräts: Patch%0*d") % (breite, 1))
    if not aenderungen:
        print(T("  Alle Ordner heißen schon so. Nichts zu tun."))
        return
    print(T("  Zu ändern: %d") % len(aenderungen))
    for alt, neu, _ in aenderungen:
        print(T("    %-12s ->  %s") % (alt, neu))
    if not schreiben:
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return
    # Zuordnung sichern, damit sich alles zurücknehmen lässt
    ordner = wurzel or os.path.expanduser(SICHERUNG_WURZEL)
    os.makedirs(ordner, exist_ok=True)
    protokoll = os.path.join(ordner, "Namensform_%s.txt" % time.strftime("%Y-%m-%d_%H-%M-%S"))
    with open(protokoll, "w", encoding="utf-8") as f:
        f.write("# Umbenannte Patch-Ordner auf %s\n" % basis)
        for alt, neu, _ in aenderungen:
            f.write("%s\t%s\n" % (alt, neu))
    fehler = 0
    for alt, neu, pfad in aenderungen:
        ziel = os.path.join(basis, neu)
        zwischen = os.path.join(basis, "tmp_%s_%d" % (neu, os.getpid()))
        try:
            os.rename(pfad, zwischen)      # erst weg vom alten Namen
            os.rename(zwischen, ziel)      # dann auf die gewünschte Form
        except OSError as e:
            fehler += 1
            print(T("    %-12s FEHLER: %s") % (alt, e.strerror))
            if os.path.exists(zwischen) and not os.path.exists(pfad):
                try:
                    os.rename(zwischen, pfad)   # zurück, falls halb umbenannt
                except OSError:
                    pass
    nach = len(patches(basis))
    print(T("  Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.")
          % (len(aenderungen) - fehler, fehler, nach))
    print(T("  Zuordnung liegt in: %s") % protokoll)


def befehl_saeubern(basis, schreiben):
    if not schreiben:
        anzahl = sum(1 for w, d, ds in os.walk(os.path.dirname(basis))
                     for x in ds if x.startswith("._") or x == ".DS_Store")
        print(T("  %d Apple-Dateien auf der Karte. Mit --schreiben entfernen.") % anzahl)
        return
    saeubere_karte(basis)
    print(T("  Fertig."))


def befehl_abspielen(basis, nummer):
    alle = dict((n, p) for n, _, p in patches(basis))
    if nummer not in alle:
        raise SystemExit("Fehler: Patch%d gibt es nicht." % nummer)
    w = phrase_wav(alle[nummer])
    if not w:
        raise SystemExit("Fehler: Patch%d hat keine WAV." % nummer)
    print(T("  Spielt \"%s\" (%s) … (Strg+C hält an)")
          % (patch_name(alle[nummer]), sekunden(wav_info(w)[0])))
    griff = abspielen_datei(w)
    try:
        if hasattr(griff, "wait"):
            griff.wait()
        else:
            import time
            while os.path.exists(w):     # Windows: warten, bis der Nutzer Ctrl+C drückt
                time.sleep(0.2)
    except KeyboardInterrupt:
        stoppen_ton(griff)
        print(T("  Angehalten."))


# --------------------------------------------------- Löschen und Verschieben

SICHERUNG_WURZEL = "~/JamMan-Sicherung"

# Englische Befehlsnamen -> deutscher Befehl. Die deutschen Namen bleiben
# gültig; die englischen sind nur ein zweiter Weg.
# Spendenlink. Leer lassen, solange es keinen gibt - das Werkzeug zeigt
# dann einen Hinweis statt einen toten Verweis zu öffnen.
SPENDEN_LINK = ("https://www.paypal.com/donate/"
                "?hosted_button_id=PEJEFMSKUPA8C&currency_code=EUR")

BEFEHL_ALIAS = {
    "list": "liste",
    "names": "namensform",
    "backup": "sichern",
    "import": "aufspielen",
    "play": "abspielen",
    "delete": "loeschen",
    "move": "verschieben",
    "swap": "tauschen",
    "place": "platzieren",
    "restore": "zuruecknehmen",
    "normalize": "alle-normieren",
    "undo-level": "pegel-umkehren",
    "split": "teilen",
    "change": "aendern",
    "clean": "saeubern",
    "eject": "auswerfen",
    "selftest": "selbsttest",
}

BEFEHLE = {
    "liste": ("List patches", "Patches auflisten"),
    "namensform": ("Normalise folder names", "Ordnernamen angleichen"),
    "sichern": ("Back up the card", "Karte sichern"),
    "aufspielen": ("Import an audio file as a patch", "Audiodatei als Patch aufspielen"),
    "abspielen": ("Play a patch", "Patch abspielen"),
    "loeschen": ("Delete a patch", "Patch löschen"),
    "verschieben": ("Move a patch", "Patch verschieben"),
    "tauschen": ("Swap two patches", "Zwei Patches tauschen"),
    "platzieren": ("Put a patch on a free slot", "Patch auf freien Platz legen"),
    "zuruecknehmen": ("Restore from a backup", "Aus Sicherung zurückholen"),
    "alle-normieren": ("Normalise all patches", "Alle Patches normieren"),
    "pegel-umkehren": ("Undo a level change", "Pegeländerung zurücknehmen"),
    "teilen": ("Split a file into several patches", "Datei in mehrere Patches teilen"),
    "aendern": ("Change name, stop mode, playback", "Name, Stoppart, Wiedergabe ändern"),
    "saeubern": ("Remove macOS clutter files", "macOS-Begleitdateien entfernen"),
    "auswerfen": ("Eject the card", "Karte auswerfen"),
    "gui": ("Open the window", "Fenster öffnen"),
    "menu": ("Text menu", "Textmenü"),
    "selbsttest": ("Report what the tool sees", "Bericht, was das Werkzeug sieht"),
}


def befehlsliste():
    """Hilfetext mit beiden Namen, in der gewählten Sprache."""
    zeilen = []
    for befehl, (englisch, deutsch) in BEFEHLE.items():
        zeilen.append("  %-14s %s" % (befehl, T(englisch if SPRACHE != "de" else deutsch)))
    return "\n".join(zeilen)

# ---------------------------------------------------------------- Sprachen
# Die Schlüssel sind die deutschen Texte selbst. Fehlt eine Übersetzung,
# erscheint der englische Text, sonst der deutsche. So bleibt das Werkzeug
# auch mit unvollständigem Katalog bedienbar.
SPRACHEN = {
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "zh": "中文",
}

TEXTE = {
    "de": {},          # Deutsch ist die Quelle

    "en": {
        'Spenden': 'Donate',
        'Für dieses Werkzeug ist noch kein Spendenlink eingetragen.': 'No donation link has been entered for this tool yet.',
        'Er steht in jamman.py in der Zeile SPENDEN_LINK.': 'It goes into jamman.py, line SPENDEN_LINK.',
        '      Schnitt %d bei %s: %s': '      Cut %d at %s: %s',
        'keine Pause in der Nähe - rechnerisch geschnitten': 'no pause nearby - cut at the calculated point',
        "Speicherkarte des JamMan Solo XT": "JamMan Solo XT memory card",
        "keine Karte gefunden": "no card found",
        "Ordner": "Folder", "Name": "Name", "Dauer": "Length",
        "Stoppart": "Stop", "Wdg.": "Play", "Rhythmus": "Rhythm",
        "Wiedergabe": "Playback",
        "Sicherung": "Backup",
        "Aufspielen": "Import",
        "Der Name darf nicht leer sein.": "The name must not be empty.",
        "Sicherung zurückholen": "Restore backup",
        "Pegel zurückrechnen": "Undo level change",
        "Bitte 2 bis 60 Teile angeben.": "Please enter 2 to 60 parts.",
        "Die Datei ist nicht lesbar.": "The file cannot be read.",
        "Datei teilen": "Split file",
        "Alle Patches normieren": "Normalise all patches",
        "Alle Patches werden auf gleiche Lautheit gebracht:\n\n"
        "  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\n"
        "Vorher wird die ganze Karte gesichert - das dauert bei\n"
        "900 MB ein bis zwei Minuten. Fortfahren?":
            "All patches are brought to the same loudness:\n\n"
            "  target -18 dB loudness, peak at most -1 dB\n\n"
            "The whole card is backed up first - about one to two\n"
            "minutes for 900 MB. Continue?",
        "Zum Tauschen genau ZWEI Patches markieren:\n"
        "ersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘).":
            "Select exactly TWO patches to swap:\n"
            "click the first, then the second with Cmd (⌘) held.",
        "Tauschen": "Swap",
        "Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern.":
            "Everywhere says \u201eas template\u201c - nothing to apply.",
        "Einstellungen anwenden": "Apply settings",
        "Erst einen Patch wählen.": "Select a patch first.",
        "Karte auswerfen? Danach kann sie abgezogen werden.":
            "Eject the card? Then it can be unplugged.",
        "Keine JamMan-Karte": "No JamMan card",
        "Datei zu lang": "File too long",
        "Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3), oder das Feld leer lassen.":
            "Enter a level in dB (e.g. -3), or leave the field empty.",
        "Bei Teile bitte eine ganze Zahl eintragen.":
            "Please enter a whole number of parts.",
        "Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3).":
            "Enter a level in dB (e.g. -3).",
        "Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.":
            "Enter the fade length in seconds.",
        "Anleitung in die Zwischenablage kopiert": "Instructions copied to the clipboard",
        "Ende der Liste erreicht": "end of list reached",
        "Pegel zurückgerechnet": "level change undone",
        "alle Patches normiert": "all patches normalised",
        # --- Kommandozeile ---
        "Fertig.": "Done.",
        "Tauschen:": "Swapping:",
        "Karte auswerfen, in den JamMan stecken — der Patch ist weg.":
            "Eject the card, put it into the JamMan — the patch is gone.",
        "Neue Reihenfolge:": "New order:",
        "JamMan Solo XT — Speicherkarte verwalten": "JamMan Solo XT — manage the memory card",
        "Pythons auf diesem Rechner:": "Pythons on this machine:",
        "Geprüfte Orte:": "Places checked:",
        "Laufwerke mit JamManSoloXT:": "Drives with JamManSoloXT:",
        "Trockenlauf — nichts geändert. Mit --schreiben ausführen.":
            "Dry run — nothing changed. Run with --schreiben to apply.",
        "Trockenlauf — nichts kopiert. Mit --schreiben ausführen.":
            "Dry run — nothing copied. Run with --schreiben to apply.",
        "Trockenlauf — nichts angelegt. Mit --schreiben ausführen.":
            "Dry run — nothing created. Run with --schreiben to apply.",
        "Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.":
            "Dry run — nothing written. Run with --schreiben to apply.",
        "Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.":
            "Dry run — nothing restored. Run with --schreiben to apply.",
        "Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.":
            "Dry run — nothing renamed. Run with --schreiben to apply.",
        "Trockenlauf — nichts getauscht. Mit --schreiben ausführen.":
            "Dry run — nothing swapped. Run with --schreiben to apply.",
        "Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.":
            "Dry run — nothing deleted. Run with --schreiben to apply.",
        "Keine Patches auf der Karte.": "No patches on the card.",
        "Suche musikalisch brauchbare Schnittstellen …":
            "Looking for musically usable cut points …",
        "Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben":
            "Header:       WAV header rewritten to plain PCM",
        "Alle Ordner heißen schon so. Nichts zu tun.":
            "All folders are already spelled that way. Nothing to do.",
        "Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:":
            "Note: these are not on the card and will be created:",
        "Nichts zu tun.": "Nothing to do.",
        "1  Patches auflisten": "1  List patches",
        "2  Patch anhören": "2  Play a patch",
        "3  Datei aufspielen": "3  Import a file",
        "4  Patch ändern (Stopp, Wiedergabe, Name)": "4  Change a patch (stop, playback, name)",
        "5  Patch verschieben": "5  Move a patch",
        "6  Patch löschen": "6  Delete a patch",
        "7  Alles sichern": "7  Back up everything",
        "8  Karte auswerfen": "8  Eject the card",
        "0  Beenden": "0  Quit",
        "Angehalten.": "Stopped.",
        "erste Zeile im Fenster:": "first line in the window:",
        "ACHTUNG: das Dateisystem liefert Lesefehler.":
            "WARNING: the file system reports read errors.",
        "Das ist kein Fehler des Werkzeugs - die Karte oder":
            "This is not a program error - the card or",
        "ihr Einhängepunkt ist beschädigt. Erste Schritte:":
            "its mount point is damaged. First steps:",
        "Ton:           afplay": "Sound:        afplay",
        "Ton:           winsound (Windows-eigen)": "Sound:        winsound (built into Windows)",
        "Hinweis: Pegelprotokoll konnte nicht geschrieben werden.":
            "Note: the level log could not be written.",
        "Sicherung beim Start: Karte unverändert, nichts zu tun.":
            "Backup on start: card unchanged, nothing to do.",
        "Pause an der Naht wird herausgenommen": "The pause at the seam is removed",
        # --- Kommandozeile, zweite Runde ---
        # --- Hilfe der Kommandozeile ---
        "Sprache der Ausgabe (Vorgabe: aus dem System)":
            "language of the output (default: from the system)",
        "Laufwerk der Karte (Vorgabe: automatisch erkannt)":
            "drive of the card (default: detected automatically)",
        "Zielordner für 'sichern'": "target folder for 'backup'",
        "Audiodatei für 'aufspielen'": "audio file for 'import'",
        "Name des Patches (Vorgabe: Dateiname)":
            "name of the patch (default: file name)",
        "Patch-Nummer": "patch number",
        "Zielplatz für 'verschieben'": "target slot for 'move'",
        "Tauschpartner für 'tauschen'": "partner slot for 'swap'",
        "Zielplatz für 'platzieren'": "target slot for 'place'",
        "Sicherungsordner für 'zuruecknehmen'": "backup folder for 'restore'",
        "Pegelprotokoll für 'pegel-umkehren'": "level log for 'undo-level'",
        "bei 'zuruecknehmen': alle Patches aus der Sicherung":
            "with 'restore': all patches from the backup",
        "Ziellautheit für 'alle-normieren' (Vorgabe -18 dB)":
            "target loudness for 'normalize' (default -18 dB)",
        "Spitzengrenze für 'alle-normieren' (Vorgabe -1 dB)":
            "peak limit for 'normalize' (default -1 dB)",
        "zu lange Datei auf 9:59 kürzen": "shorten a too long file to 9:59",
        "beim Teilen die Pause an der Naht stehen lassen":
            "keep the pause at the seam when splitting",
        "beim Teilen gleichmäßig schneiden statt an Pausen":
            "split evenly instead of at pauses",
        "Datei in N gleich lange Patches aufteilen":
            "split the file into N equal patches",
        "zu lange Datei auf diese Länge kürzen": "shorten the file to this length",
        "auch Loops über 10 Minuten schreiben (Pedal nimmt sie nicht an)":
            "also write loops over 10 minutes (the pedal rejects them)",
        "Fade am Ende der Datei einrechnen (z. B. 3)":
            "include a fade at the end of the file (e.g. 3)",
        "Lautstärke um DB ändern (z. B. -3 oder +2)":
            "change the volume by DB (e.g. -3 or +2)",
        "Anhebung NICHT auf 0 dBFS begrenzen (kann übersteuern)":
            "do NOT limit the boost to 0 dBFS (may clip)",
        "auf -1 dB Spitzenpegel normieren": "normalise to -1 dB peak",
        "Stopp-Verhalten des neuen Patches (ohne Angabe: wie Vorlage)":
            "stop behaviour of the new patch (default: as template)",
        "Loop oder Single (IsLoop in phrase.xml; ohne Angabe: wie Vorlage)":
            "loop or single (IsLoop in phrase.xml; default: as template)",
        "Rhythmus des neuen Patches (ohne Angabe: wie Vorlage)":
            "rhythm of the new patch (default: as template)",
        "beim Verschieben nur die Namen sichern (schnell, kein Kopieren)":
            "when moving, only save the names (fast, no copying)",
        "Ordner für die Sicherung vor Löschen/Verschieben":
            "folder for the backup before delete/move",
        "wirklich verändern (sonst Trockenlauf)": "actually change (otherwise dry run)",
        "was zu tun ist (Liste unten; englische Namen gehen auch)":
            "what to do (list below; English names work too)",
        "Quelle:       %s": "Source:       %s",
        "Quelle: %s": "Source: %s",
        "Ziel:   %s": "Target: %s",
        "Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB":
            "Target: %.1f dB loudness (RMS), peak at most %.1f dB",
        "Karte: %s": "Card: %s",
        "Name:         %s": "Name:         %s",
        "Dauer:        %s": "Length:       %s",
        "Dauer:  %s": "Length: %s",
        "Datei:  %s": "File:  %s",
        "Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s":
            "Format:       %d Hz / %d bit / %d channel(s)%s",
        "Fassung:       %s": "Version:      %s",
        "Python:        %s": "Python:       %s",
        "System:        %s %s": "System:       %s %s",
        "Tk:            %s": "Tk:           %s",
        "Tk-Modul:      %s": "Tk module:    %s",
        "Schrift:       %s": "Font:         %s",
        "Kartenwurzeln: %s": "Card roots:   %s",
        "ffmpeg:        %s": "ffmpeg:       %s",
        "gewählt:       %s": "chosen:       %s",
        "Ton:           %s": "Sound:        %s",
        "Patches:       %d": "Patches:      %d",
        "Patches: %d": "Patches: %d",
        "Patches auf der Karte:    %d": "Patches on the card:    %d",
        "Patches in der Sicherung: %d": "Patches in the backup:  %d",
        "Inhalt von %s:": "Contents of %s:",
        "Gesichert: %d Patches, %.0f MB.": "Backed up: %d patches, %.0f MB.",
        "gesichert: %d Patches, %.0f MB": "backed up: %d patches, %.0f MB",
        "Fertig. Sicherung: %s": "Done. Backup: %s",
        "Fertig: %s angelegt.": "Done: %s created.",
        "Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.":
            "Done: %d folders renamed, %d errors, %d patches readable.",
        "Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.":
            "Done: %d patches normalised, %d of them peak-limited.",
        "Gelöscht. Sicherung: %s": "Deleted. Backup: %s",
        "Getauscht. Sicherung: %s": "Swapped. Backup: %s",
        "Geändert. Sicherung: %s": "Changed. Backup: %s",
        "Verschoben. Sicherung: %s": "Moved. Backup: %s",
        "Zurückgeholt. Der Zustand davor liegt in: %s":
            "Restored. The previous state is in: %s",
        "Zurückgerechnet: %d Patches. Sicherung davor: %s":
            "Undone: %d patches. Backup before: %s",
        "Vorher wird er gesichert nach: %s": "It is backed up first to: %s",
        "Wird gelöscht: %s  (%s)": "Will be deleted: %s  (%s)",
        "Neuer Patch:  Patch%s  (Name: %s)": "New patch:  Patch%s  (name: %s)",
        "Patch:        %s  (%s)": "Patch:        %s  (%s)",
        "Patch:      %s  (%s)": "Patch:      %s  (%s)",
        "Vorlage:      %s": "Template:     %s",
        "Teile:  %d × etwa %s": "Parts:  %d × about %s",
        "Stopp:        %s": "Stop:         %s",
        "Stopp:        %s (%s)": "Stop:         %s (%s)",
        "Rhythmus:     %s": "Rhythm:       %s",
        "Rhythmus:     %s (%s)": "Rhythm:       %s (%s)",
        "Wiedergabe:   %s": "Playback:     %s",
        "Wiedergabe:   %s (IsLoop %s)": "Playback:     %s (IsLoop %s)",
        "Pegel:        %+.1f dB": "Level:        %+.1f dB",
        "Pegel:        %.1f dB → %.1f dB Spitze":
            "Level:        %.1f dB → %.1f dB peak",
        "Pegel:        auf -1 dB Spitze normieren":
            "Level:        normalise to -1 dB peak",
        "Fade:         letzte %.1f s werden ausgeblendet":
            "Fade:         last %.1f s are faded out",
        "Fade:         letzte %.1f s der Aufnahme ausblenden":
            "Fade:         fade out the last %.1f s",
        "Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)":
            "Trim:         cut to %s (hard cut, 12 ms faded out)",
        "Protokoll: %s": "Log: %s",
        "Sicherung: %s": "Backup: %s",
        "Sicherung:  %s": "Backup:  %s",
        "Schreibweise des Geräts: Patch%0*d":
            "Spelling used by the device: Patch%0*d",
        "Zu ändern: %d": "To change: %d",
        "Zuordnung liegt in: %s": "Mapping is in: %s",
        "Betroffene Einträge (erste drei): %s": "Affected entries (first three): %s",
        "Keine Karte gefunden unter %s.": "No card found under %s.",
        "wie Vorlage": "as template",
        "   ← wird umgewandelt": "   ← will be converted",
        "   ← abweichend": "   ← deviates",
        "   ← über 10 min, Pedal nimmt sie nicht an":
            "   ← over 10 min, the pedal will not accept it",
        "keine WAV": "no WAV",
        "  Das Fenster wäre leer. Es geht im Textmenü weiter.":
            "  The window would be empty. The text menu still works.",
        "  Für ein Fenster: Python von python.org installieren (bringt Tk 8.6).":
            "  For a window: install Python from python.org (ships Tk 8.6).",
        "  Tk %s kann auf diesem macOS keinen Text im Fenster zeichnen.":
            "  Tk %s cannot draw text in a window on this macOS.",
        "  Hinweis: Fade konnte nicht eingerechnet werden.":
            "  Note: the fade could not be applied.",
        "  Hinweis: Pegel ließ sich nicht ändern (kein 16-Bit-WAV?).":
            "  Note: the level could not be changed (not a 16-bit WAV?).",
        "  Achtung: der WAV-Kopf ist weiterhin ungewöhnlich.":
            "  Careful: the WAV header is still unusual.",
        "  Hinweis: Die Vorlage hatte kein Feld IsLoop - es wurde ergänzt.":
            "  Note: the template had no IsLoop field - it was added.",
        "  Hinweis: Die Vorlage hatte kein Feld StopMode - es wurde ergänzt.":
            "  Note: the template had no StopMode field - it was added.",
        "  Hinweis: Die Vorlage hatte kein Feld RhythmType - es wurde ergänzt.":
            "  Note: the template had no RhythmType field - it was added.",
        "  Angehalten.": "  Stopped.",
        "  Das ist kein Fehler des Werkzeugs - die Karte oder":
            "  This is not a program error - the card or",
        "  ihr Einhängepunkt ist beschädigt. Erste Schritte:":
            "  its mount point is damaged. First steps:",
        "    dmesg | tail -20            (Meldungen des Systems)":
            "    dmesg | tail -20            (system messages)",
        "    sudo umount <laufwerk>      (aushängen)":
            "    sudo umount <device>        (unmount)",
        "    sudo fsck.vfat -n <geraet>  (FAT32, nur prüfen)":
            "    sudo fsck.vfat -n <device>  (FAT32, check only)",
        "    sudo exfatfsck <geraet>     (exFAT, nur prüfen)":
            "    sudo exfatfsck <device>     (exFAT, check only)",
        "    oder die Karte im JamMan einschalten - das Pedal":
            "    or put the card into the JamMan and switch it on -",
        "    repariert seine Karte beim Einschalten.":
            "    the pedal repairs its card at start-up.",
        "Patch markieren — dann hier klicken zum Springen":
            "Select a patch — then click here to jump",
        "kein Patch gewählt": "no patch selected",
        "automatisch weiter": "continue automatically",
        "beim Start sichern": "back up on start",
        "Platz": "Slot",
        "nächster": "next",
        "vormerken": "cue",
        "vorgemerkt %d": "cued %d",
        "Einstellungen:": "Settings:",
        "wie Vorlage": "as template",
        "Fade am Ende": "Fade at end",
        "Karte wählen": "Choose card",
        "Datei aufspielen": "Import file",
        "Alles sichern": "Back up all",
        "Pegel zurückrechnen": "Undo level change",
        "Neu laden": "Reload",
        "Anhören": "Play",
        "Stopp": "Stop",
        "Umbenennen": "Rename",
        "Platz ändern": "Change slot",
        "Sicherung zurückholen": "Restore backup",
        "Einstellungen anwenden": "Apply settings",
        "▲ nach oben": "▲ move up",
        "▼ nach unten": "▼ move down",
        "Tauschen": "Swap",
        "Datei teilen": "Split file",
        "Alle normieren": "Normalise all",
        "Löschen": "Delete",
        "Karte auswerfen": "Eject card",
        "Pegel": "Level",
        "auf −1 dB Spitze normieren": "normalise to −1 dB peak",
        "Teile": "Parts",
        "an Pausen schneiden": "cut at pauses",
        "Anhebung auf 0 dB begrenzen": "limit boost to 0 dB",
        "Doppelklick spielt · Ziehen ordnet um · ⌘ für zwei Markierungen, dann Tauschen · Esc hält an.":
            "Double-click plays · drag reorders · ⌘ for two selections, then swap · Esc stops.",
        "vorgemerkt: Platz %d": "cued: slot %d",
        "Ende der Liste erreicht": "end of list reached",
        "Erst einen Patch in der Liste wählen.": "Select a patch in the list first.",
        "Erst eine Karte wählen.": "Choose a card first.",
        "Bitte eine Platznummer eintragen.": "Please enter a slot number.",
        "Platz %d gibt es auf dieser Karte nicht.": "Slot %d does not exist on this card.",
        "MP3 und M4A brauchen ffmpeg -": "MP3 and M4A need ffmpeg -",
        "Anleitung": "Instructions",
        "WAV geht auch ohne": "WAV works without it",
        "Schließen": "Close",
        "Text kopieren": "Copy text",
        "Karte nicht lesbar": "Card not readable",
        "Fehler": "Error",
        "Hinweis": "Note",
        "Anleitung in die Zwischenablage kopiert": "Instructions copied to the clipboard",
        "Sicherung beim Start: %s": "Backup on start: %s",
    },
    "fr": {
        'Spenden': 'Faire un don',
        'Für dieses Werkzeug ist noch kein Spendenlink eingetragen.': "Aucun lien de don n'est encore renseigné pour cet outil.",
        'Er steht in jamman.py in der Zeile SPENDEN_LINK.': 'Il se place dans jamman.py, ligne SPENDEN_LINK.',
        '      Schnitt %d bei %s: %s': '      Coupe %d à %s : %s',
        'keine Pause in der Nähe - rechnerisch geschnitten': 'aucune pause à proximité — coupe calculée',
        "Tk:            %s": "Tk:            %s",
        'Sicherung': 'Sauvegarde',
        'Aufspielen': 'Importer',
        'Der Name darf nicht leer sein.': 'Le nom ne doit pas être vide.',
        'Bitte 2 bis 60 Teile angeben.': 'Indiquer de 2 à 60 parties.',
        'Die Datei ist nicht lesbar.': 'Le fichier est illisible.',
        'Alle Patches normieren': 'Normaliser tous les patches',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nVorher wird die ganze Karte gesichert - das dauert bei\n900 MB ein bis zwei Minuten. Fortfahren?': "Tous les patches sont amenés à la même sonie :\n\n  cible -18 dB de sonie, crête au plus -1 dB\n\nLa carte est d'abord sauvegardée - cela prend\nune à deux minutes pour 900 Mo. Continuer ?",
        'Zum Tauschen genau ZWEI Patches markieren:\nersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘).': 'Pour échanger, sélectionner exactement DEUX patches :\ncliquer le premier, puis le second avec Cmd (⌘) enfoncé.',
        'Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern.': "Partout « comme le modèle » - il n'y a rien à appliquer.",
        'Erst einen Patch wählen.': "Choisir d'abord un patch.",
        'Karte auswerfen? Danach kann sie abgezogen werden.': 'Éjecter la carte ? Elle pourra ensuite être retirée.',
        'Keine JamMan-Karte': 'Pas de carte JamMan',
        'Datei zu lang': 'Fichier trop long',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3), oder das Feld leer lassen.': 'Entrer un niveau en dB (par ex. -3), ou laisser le champ vide.',
        'Bei Teile bitte eine ganze Zahl eintragen.': 'Indiquer un nombre entier de parties.',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3).': 'Entrer un niveau en dB (par ex. -3).',
        'Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.': 'Indiquer la durée du fondu en secondes.',
        'Pegel zurückgerechnet': 'niveau annulé',
        'alle Patches normiert': 'tous les patches normalisés',
        'Fertig.': 'Terminé.',
        'Tauschen:': 'Échange :',
        'Karte auswerfen, in den JamMan stecken — der Patch ist weg.': 'Éjecter la carte, la mettre dans le JamMan — le patch a disparu.',
        'Neue Reihenfolge:': 'Nouvel ordre :',
        'JamMan Solo XT — Speicherkarte verwalten': 'JamMan Solo XT — gérer la carte mémoire',
        'Pythons auf diesem Rechner:': 'Pythons sur cette machine :',
        'Geprüfte Orte:': 'Emplacements vérifiés :',
        'Laufwerke mit JamManSoloXT:': 'Lecteurs avec JamManSoloXT :',
        'Trockenlauf — nichts geändert. Mit --schreiben ausführen.': 'Simulation — rien modifié. Exécuter avec --schreiben.',
        'Trockenlauf — nichts kopiert. Mit --schreiben ausführen.': 'Simulation — rien copié. Exécuter avec --schreiben.',
        'Trockenlauf — nichts angelegt. Mit --schreiben ausführen.': 'Simulation — rien créé. Exécuter avec --schreiben.',
        'Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.': 'Simulation — rien écrit. Exécuter avec --schreiben.',
        'Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.': 'Simulation — rien restauré. Exécuter avec --schreiben.',
        'Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.': 'Simulation — rien renommé. Exécuter avec --schreiben.',
        'Trockenlauf — nichts getauscht. Mit --schreiben ausführen.': 'Simulation — rien échangé. Exécuter avec --schreiben.',
        'Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.': 'Simulation — rien supprimé. Exécuter avec --schreiben.',
        'Keine Patches auf der Karte.': 'Aucun patch sur la carte.',
        'Suche musikalisch brauchbare Schnittstellen …': 'Recherche de points de coupe musicaux …',
        'Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben': 'En-tête : en-tête WAV réécrit en PCM simple',
        'Alle Ordner heißen schon so. Nichts zu tun.': 'Tous les dossiers portent déjà ce nom. Rien à faire.',
        'Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:': 'Remarque : ceux-ci ne sont pas sur la carte et seront créés :',
        'Nichts zu tun.': 'Rien à faire.',
        '1  Patches auflisten': '1  Lister les patches',
        '2  Patch anhören': '2  Écouter un patch',
        '3  Datei aufspielen': '3  Importer un fichier',
        '4  Patch ändern (Stopp, Wiedergabe, Name)': '4  Modifier un patch (arrêt, lecture, nom)',
        '5  Patch verschieben': '5  Déplacer un patch',
        '6  Patch löschen': '6  Supprimer un patch',
        '7  Alles sichern': '7  Tout sauvegarder',
        '8  Karte auswerfen': '8  Éjecter la carte',
        '0  Beenden': '0  Quitter',
        'Angehalten.': 'Arrêté.',
        'erste Zeile im Fenster:': 'première ligne dans la fenêtre :',
        'ACHTUNG: das Dateisystem liefert Lesefehler.': 'ATTENTION : le système de fichiers signale des erreurs de lecture.',
        'Das ist kein Fehler des Werkzeugs - die Karte oder': "Ce n'est pas une erreur du programme — la carte ou",
        'ihr Einhängepunkt ist beschädigt. Erste Schritte:': 'son point de montage est endommagé. Premiers pas :',
        'Ton:           afplay': 'Son :         afplay',
        'Ton:           winsound (Windows-eigen)': 'Son :         winsound (intégré à Windows)',
        'Hinweis: Pegelprotokoll konnte nicht geschrieben werden.': "Remarque : le journal des niveaux n'a pas pu être écrit.",
        'Sicherung beim Start: Karte unverändert, nichts zu tun.': 'Sauvegarde au démarrage : carte inchangée, rien à faire.',
        'Pause an der Naht wird herausgenommen': 'La pause à la jonction est supprimée',
        'Quelle:       %s': 'Source :       %s',
        'Quelle: %s': 'Source : %s',
        'Ziel:   %s': 'Cible :   %s',
        'Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB': 'Cible : %.1f dB de sonie (RMS), crête au plus %.1f dB',
        'Karte: %s': 'Carte : %s',
        'Name:         %s': 'Nom :         %s',
        'Dauer:        %s': 'Durée :       %s',
        'Dauer:  %s': 'Durée : %s',
        'Datei:  %s': 'Fichier :  %s',
        'Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s': 'Format :       %d Hz / %d bits / %d canal(aux)%s',
        'Fassung:       %s': 'Version :     %s',
        'Python:        %s': 'Python :      %s',
        'System:        %s %s': 'Système :     %s %s',
        'Tk-Modul:      %s': 'Module Tk :   %s',
        'Schrift:       %s': 'Police :      %s',
        'Kartenwurzeln: %s': 'Racines des cartes : %s',
        'ffmpeg:        %s': 'ffmpeg :      %s',
        'gewählt:       %s': 'choisi :      %s',
        'Ton:           %s': 'Son :         %s',
        'Patches:       %d': 'Patches :     %d',
        'Patches: %d': 'Patches : %d',
        'Patches auf der Karte:    %d': 'Patches sur la carte :    %d',
        'Patches in der Sicherung: %d': 'Patches dans la sauvegarde : %d',
        'Inhalt von %s:': 'Contenu de %s :',
        'Gesichert: %d Patches, %.0f MB.': 'Sauvegardé : %d patches, %.0f Mo.',
        'gesichert: %d Patches, %.0f MB': 'sauvegardé : %d patches, %.0f Mo',
        'Fertig. Sicherung: %s': 'Terminé. Sauvegarde : %s',
        'Fertig: %s angelegt.': 'Terminé : %s créé.',
        'Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.': 'Terminé : %d dossiers renommés, %d erreurs, %d patches lisibles.',
        'Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.': 'Terminé : %d patches normalisés, %d limités en crête.',
        'Gelöscht. Sicherung: %s': 'Supprimé. Sauvegarde : %s',
        'Getauscht. Sicherung: %s': 'Échangé. Sauvegarde : %s',
        'Geändert. Sicherung: %s': 'Modifié. Sauvegarde : %s',
        'Verschoben. Sicherung: %s': 'Déplacé. Sauvegarde : %s',
        'Zurückgeholt. Der Zustand davor liegt in: %s': "Restauré. L'état précédent se trouve dans : %s",
        'Zurückgerechnet: %d Patches. Sicherung davor: %s': 'Annulé : %d patches. Sauvegarde avant : %s',
        'Vorher wird er gesichert nach: %s': "Il est d'abord sauvegardé dans : %s",
        'Wird gelöscht: %s  (%s)': 'Sera supprimé : %s  (%s)',
        'Neuer Patch:  Patch%s  (Name: %s)': 'Nouveau patch : Patch%s  (nom : %s)',
        'Patch:        %s  (%s)': 'Patch :        %s  (%s)',
        'Patch:      %s  (%s)': 'Patch :      %s  (%s)',
        'Vorlage:      %s': 'Modèle :      %s',
        'Teile:  %d × etwa %s': 'Parties :  %d × environ %s',
        'Stopp:        %s': 'Arrêt :        %s',
        'Stopp:        %s (%s)': 'Arrêt :        %s (%s)',
        'Rhythmus:     %s': 'Rythme :       %s',
        'Rhythmus:     %s (%s)': 'Rythme :       %s (%s)',
        'Wiedergabe:   %s': 'Lecture :      %s',
        'Wiedergabe:   %s (IsLoop %s)': 'Lecture :      %s (IsLoop %s)',
        'Pegel:        %+.1f dB': 'Niveau :      %+.1f dB',
        'Pegel:        %.1f dB → %.1f dB Spitze': 'Niveau :      %.1f dB → %.1f dB crête',
        'Pegel:        auf -1 dB Spitze normieren': 'Niveau :      normaliser à -1 dB crête',
        'Fade:         letzte %.1f s werden ausgeblendet': 'Fondu :       les %.1f dernières s sont atténuées',
        'Fade:         letzte %.1f s der Aufnahme ausblenden': 'Fondu :       atténuer les %.1f dernières s',
        'Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)': 'Raccourci :   coupé à %s (coupe franche, 12 ms atténuées)',
        'Protokoll: %s': 'Journal : %s',
        'Sicherung: %s': 'Sauvegarde : %s',
        'Sicherung:  %s': 'Sauvegarde :  %s',
        'Schreibweise des Geräts: Patch%0*d': "Orthographe de l'appareil : Patch%0*d",
        'Zu ändern: %d': 'À modifier : %d',
        'Zuordnung liegt in: %s': 'Correspondance dans : %s',
        'Betroffene Einträge (erste drei): %s': 'Entrées concernées (trois premières) : %s',
        'Keine Karte gefunden unter %s.': 'Aucune carte trouvée sous %s.',
        '   ← wird umgewandelt': '← sera converti',
        '   ← abweichend': '← différent',
        '   ← über 10 min, Pedal nimmt sie nicht an': "← plus de 10 min, la pédale ne l'accepte pas",
        'keine WAV': 'pas de WAV',
        '  Das Fenster wäre leer. Es geht im Textmenü weiter.': 'La fenêtre serait vide. Le menu texte fonctionne encore.',
        '  Für ein Fenster: Python von python.org installieren (bringt Tk 8.6).': 'Pour une fenêtre : installer Python depuis python.org (fournit Tk 8.6).',
        '  Tk %s kann auf diesem macOS keinen Text im Fenster zeichnen.': 'Tk %s ne peut pas dessiner de texte dans une fenêtre sous ce macOS.',
        '  Hinweis: Fade konnte nicht eingerechnet werden.': "Remarque : le fondu n'a pas pu être appliqué.",
        '  Hinweis: Pegel ließ sich nicht ändern (kein 16-Bit-WAV?).': "Remarque : le niveau n'a pas pu être modifié (WAV 16 bits ?).",
        '  Achtung: der WAV-Kopf ist weiterhin ungewöhnlich.': "Attention : l'en-tête WAV reste inhabituel.",
        '  Hinweis: Die Vorlage hatte kein Feld IsLoop - es wurde ergänzt.': "Remarque : le modèle n'avait pas de champ IsLoop — il a été ajouté.",
        '  Hinweis: Die Vorlage hatte kein Feld StopMode - es wurde ergänzt.': "Remarque : le modèle n'avait pas de champ StopMode — il a été ajouté.",
        '  Hinweis: Die Vorlage hatte kein Feld RhythmType - es wurde ergänzt.': "Remarque : le modèle n'avait pas de champ RhythmType — il a été ajouté.",
        '  Angehalten.': 'Arrêté.',
        '  Das ist kein Fehler des Werkzeugs - die Karte oder': "Ce n'est pas une erreur du programme — la carte ou",
        '  ihr Einhängepunkt ist beschädigt. Erste Schritte:': 'son point de montage est endommagé. Premiers pas :',
        '    dmesg | tail -20            (Meldungen des Systems)': 'dmesg | tail -20            (messages du système)',
        '    sudo umount <laufwerk>      (aushängen)': 'sudo umount <périphérique>  (démonter)',
        '    sudo fsck.vfat -n <geraet>  (FAT32, nur prüfen)': 'sudo fsck.vfat -n <périph>  (FAT32, vérifier seulement)',
        '    sudo exfatfsck <geraet>     (exFAT, nur prüfen)': 'sudo exfatfsck <périph>     (exFAT, vérifier seulement)',
        '    oder die Karte im JamMan einschalten - das Pedal': "ou mettre la carte dans le JamMan et l'allumer —",
        '    repariert seine Karte beim Einschalten.': 'la pédale répare sa carte au démarrage.',
        'Erst eine Karte wählen.': "Choisir d'abord une carte.",
        "Speicherkarte des JamMan Solo XT": "Carte mémoire du JamMan Solo XT",
        "keine Karte gefunden": "aucune carte trouvée",
        "Ordner": "Dossier", "Name": "Nom", "Dauer": "Durée",
        "Stoppart": "Arrêt", "Wdg.": "Lecture", "Rhythmus": "Rythme",
        "Wiedergabe": "Lecture",
        "Patch markieren — dann hier klicken zum Springen":
            "Choisir un patch — puis cliquer ici pour y aller",
        "kein Patch gewählt": "aucun patch sélectionné",
        "automatisch weiter": "enchaîner automatiquement",
        "beim Start sichern": "sauvegarder au démarrage",
        "Platz": "Emplacement",
        "nächster": "suivant",
        "vormerken": "préparer",
        "vorgemerkt %d": "préparé %d",
        "Einstellungen:": "Réglages :",
        "wie Vorlage": "comme le modèle",
        "Fade am Ende": "Fondu à la fin",
        "Karte wählen": "Choisir la carte",
        "Datei aufspielen": "Importer un fichier",
        "Alles sichern": "Tout sauvegarder",
        "Pegel zurückrechnen": "Annuler le niveau",
        "Neu laden": "Recharger",
        "Anhören": "Écouter",
        "Stopp": "Arrêter",
        "Umbenennen": "Renommer",
        "Platz ändern": "Changer l'emplacement",
        "Sicherung zurückholen": "Restaurer la sauvegarde",
        "Einstellungen anwenden": "Appliquer les réglages",
        "▲ nach oben": "▲ monter",
        "▼ nach unten": "▼ descendre",
        "Tauschen": "Échanger",
        "Datei teilen": "Diviser le fichier",
        "Alle normieren": "Tout normaliser",
        "Löschen": "Supprimer",
        "Karte auswerfen": "Éjecter la carte",
        "Pegel": "Niveau",
        "auf −1 dB Spitze normieren": "normaliser à −1 dB crête",
        "Teile": "Parties",
        "an Pausen schneiden": "couper aux silences",
        "Anhebung auf 0 dB begrenzen": "limiter la hausse à 0 dB",
        "Doppelklick spielt · Ziehen ordnet um · ⌘ für zwei Markierungen, dann Tauschen · Esc hält an.":
            "Double-clic lecture · glisser réordonne · ⌘ pour deux sélections, puis échanger · Échap arrête.",
        "vorgemerkt: Platz %d": "préparé : emplacement %d",
        "Ende der Liste erreicht": "fin de la liste",
        "Erst einen Patch in der Liste wählen.": "Choisir d'abord un patch dans la liste.",
        "Bitte eine Platznummer eintragen.": "Entrer un numéro d'emplacement.",
        "Platz %d gibt es auf dieser Karte nicht.": "L'emplacement %d n'existe pas sur cette carte.",
        "MP3 und M4A brauchen ffmpeg -": "MP3 et M4A nécessitent ffmpeg -",
        "Anleitung": "Instructions",
        "WAV geht auch ohne": "WAV fonctionne sans",
        "Schließen": "Fermer",
        "Text kopieren": "Copier le texte",
        "Karte nicht lesbar": "Carte illisible",
        "Fehler": "Erreur",
        "Hinweis": "Remarque",
        "Anleitung in die Zwischenablage kopiert": "Instructions copiées",
        "Sicherung beim Start: %s": "Sauvegarde au démarrage : %s",
    },
    "es": {
        'Spenden': 'Donar',
        'Für dieses Werkzeug ist noch kein Spendenlink eingetragen.': 'Todavía no hay un enlace de donación para esta herramienta.',
        'Er steht in jamman.py in der Zeile SPENDEN_LINK.': 'Se introduce en jamman.py, línea SPENDEN_LINK.',
        '      Schnitt %d bei %s: %s': '      Corte %d en %s: %s',
        'keine Pause in der Nähe - rechnerisch geschnitten': 'no hay pausa cerca — corte calculado',
        "Tk:            %s": "Tk:            %s",
        'Sicherung': 'copia de seguridad',
        'Aufspielen': 'Importar',
        'Der Name darf nicht leer sein.': 'El nombre no puede estar vacío.',
        'Bitte 2 bis 60 Teile angeben.': 'Indica de 2 a 60 partes.',
        'Die Datei ist nicht lesbar.': 'El archivo no se puede leer.',
        'Alle Patches normieren': 'Normalizar todos los patches',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nVorher wird die ganze Karte gesichert - das dauert bei\n900 MB ein bis zwei Minuten. Fortfahren?': 'Todos los patches se igualan en sonoridad:\n\n  objetivo -18 dB de sonoridad, pico como máximo -1 dB\n\nAntes se copia toda la tarjeta - tarda uno o dos\nminutos para 900 MB. ¿Continuar?',
        'Zum Tauschen genau ZWEI Patches markieren:\nersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘).': 'Para intercambiar, marca exactamente DOS patches:\nhaz clic en el primero y luego en el segundo con Cmd (⌘) pulsada.',
        'Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern.': 'Arriba pone «como plantilla» - no hay nada que aplicar.',
        'Erst einen Patch wählen.': 'Elige primero un patch.',
        'Karte auswerfen? Danach kann sie abgezogen werden.': '¿Expulsar la tarjeta? Después se puede retirar.',
        'Keine JamMan-Karte': 'No es una tarjeta JamMan',
        'Datei zu lang': 'Archivo demasiado largo',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3), oder das Feld leer lassen.': 'Introduce un nivel en dB (p. ej. -3) o deja el campo vacío.',
        'Bei Teile bitte eine ganze Zahl eintragen.': 'Introduce un número entero de partes.',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3).': 'Introduce un nivel en dB (p. ej. -3).',
        'Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.': 'Introduce la duración del fundido en segundos.',
        'Pegel zurückgerechnet': 'nivel deshecho',
        'alle Patches normiert': 'todos los patches normalizados',
        'Fertig.': 'Hecho.',
        'Tauschen:': 'Intercambiar:',
        'Karte auswerfen, in den JamMan stecken — der Patch ist weg.': 'Expulsa la tarjeta y pónla en el JamMan — el patch desaparece.',
        'Neue Reihenfolge:': 'Nuevo orden:',
        'JamMan Solo XT — Speicherkarte verwalten': 'JamMan Solo XT — gestionar la tarjeta de memoria',
        'Pythons auf diesem Rechner:': 'Pythons en este equipo:',
        'Geprüfte Orte:': 'Lugares comprobados:',
        'Laufwerke mit JamManSoloXT:': 'Unidades con JamManSoloXT:',
        'Trockenlauf — nichts geändert. Mit --schreiben ausführen.': 'Simulación — nada cambiado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts kopiert. Mit --schreiben ausführen.': 'Simulación — nada copiado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts angelegt. Mit --schreiben ausführen.': 'Simulación — nada creado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.': 'Simulación — nada escrito. Ejecuta con --schreiben.',
        'Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.': 'Simulación — nada restaurado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.': 'Simulación — nada renombrado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts getauscht. Mit --schreiben ausführen.': 'Simulación — nada intercambiado. Ejecuta con --schreiben.',
        'Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.': 'Simulación — nada borrado. Ejecuta con --schreiben.',
        'Keine Patches auf der Karte.': 'No hay patches en la tarjeta.',
        'Suche musikalisch brauchbare Schnittstellen …': 'Buscando puntos de corte musicales …',
        'Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben': 'Cabecera:     cabecera WAV reescrita a PCM simple',
        'Alle Ordner heißen schon so. Nichts zu tun.': 'Todas las carpetas ya se llaman así. Nada que hacer.',
        'Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:': 'Nota: estos no están en la tarjeta y se crearán:',
        'Nichts zu tun.': 'Nada que hacer.',
        '1  Patches auflisten': '1  Listar patches',
        '2  Patch anhören': '2  Escuchar un patch',
        '3  Datei aufspielen': '3  Importar un archivo',
        '4  Patch ändern (Stopp, Wiedergabe, Name)': '4  Cambiar un patch (parada, reproducción, nombre)',
        '5  Patch verschieben': '5  Mover un patch',
        '6  Patch löschen': '6  Borrar un patch',
        '7  Alles sichern': '7  Copiar todo',
        '8  Karte auswerfen': '8  Expulsar la tarjeta',
        '0  Beenden': '0  Salir',
        'Angehalten.': 'Detenido.',
        'erste Zeile im Fenster:': 'primera línea en la ventana:',
        'ACHTUNG: das Dateisystem liefert Lesefehler.': 'ATENCIÓN: el sistema de archivos da errores de lectura.',
        'Das ist kein Fehler des Werkzeugs - die Karte oder': 'No es un error del programa — la tarjeta o',
        'ihr Einhängepunkt ist beschädigt. Erste Schritte:': 'su punto de montaje está dañado. Primeros pasos:',
        'Ton:           afplay': 'Sonido:        afplay',
        'Ton:           winsound (Windows-eigen)': 'Sonido:        winsound (incluido en Windows)',
        'Hinweis: Pegelprotokoll konnte nicht geschrieben werden.': 'Nota: no se pudo escribir el registro de niveles.',
        'Sicherung beim Start: Karte unverändert, nichts zu tun.': 'Copia al iniciar: tarjeta sin cambios, nada que hacer.',
        'Pause an der Naht wird herausgenommen': 'La pausa en la unión se elimina',
        'Quelle:       %s': 'Origen:        %s',
        'Quelle: %s': 'Origen: %s',
        'Ziel:   %s': 'Destino:   %s',
        'Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB': 'Destino: %.1f dB de sonoridad (RMS), pico como máximo %.1f dB',
        'Karte: %s': 'Tarjeta: %s',
        'Name:         %s': 'Nombre:       %s',
        'Dauer:        %s': 'Duración:     %s',
        'Dauer:  %s': 'Duración: %s',
        'Datei:  %s': 'Archivo:  %s',
        'Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s': 'Formato:      %d Hz / %d bits / %d canal(es)%s',
        'Fassung:       %s': 'Versión:      %s',
        'Python:        %s': 'Python:       %s',
        'System:        %s %s': 'Sistema:      %s %s',
        'Tk-Modul:      %s': 'Módulo Tk:    %s',
        'Schrift:       %s': 'Fuente:       %s',
        'Kartenwurzeln: %s': 'Raíces de tarjetas: %s',
        'ffmpeg:        %s': 'ffmpeg:       %s',
        'gewählt:       %s': 'elegido:      %s',
        'Ton:           %s': 'Sonido:       %s',
        'Patches:       %d': 'Patches:      %d',
        'Patches: %d': 'Patches: %d',
        'Patches auf der Karte:    %d': 'Patches en la tarjeta:    %d',
        'Patches in der Sicherung: %d': 'Patches en la copia:      %d',
        'Inhalt von %s:': 'Contenido de %s:',
        'Gesichert: %d Patches, %.0f MB.': 'Copiado: %d patches, %.0f MB.',
        'gesichert: %d Patches, %.0f MB': 'copiado: %d patches, %.0f MB',
        'Fertig. Sicherung: %s': 'Hecho. Copia: %s',
        'Fertig: %s angelegt.': 'Hecho: %s creado.',
        'Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.': 'Hecho: %d carpetas renombradas, %d errores, %d patches legibles.',
        'Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.': 'Hecho: %d patches normalizados, %d limitados en pico.',
        'Gelöscht. Sicherung: %s': 'Borrado. Copia: %s',
        'Getauscht. Sicherung: %s': 'Intercambiado. Copia: %s',
        'Geändert. Sicherung: %s': 'Cambiado. Copia: %s',
        'Verschoben. Sicherung: %s': 'Movido. Copia: %s',
        'Zurückgeholt. Der Zustand davor liegt in: %s': 'Restaurado. El estado anterior está en: %s',
        'Zurückgerechnet: %d Patches. Sicherung davor: %s': 'Deshecho: %d patches. Copia anterior: %s',
        'Vorher wird er gesichert nach: %s': 'Antes se copia a: %s',
        'Wird gelöscht: %s  (%s)': 'Se borrará: %s  (%s)',
        'Neuer Patch:  Patch%s  (Name: %s)': 'Nuevo patch: Patch%s  (nombre: %s)',
        'Patch:        %s  (%s)': 'Patch:        %s  (%s)',
        'Patch:      %s  (%s)': 'Patch:      %s  (%s)',
        'Vorlage:      %s': 'Plantilla:    %s',
        'Teile:  %d × etwa %s': 'Partes:  %d × unos %s',
        'Stopp:        %s': 'Parada:       %s',
        'Stopp:        %s (%s)': 'Parada:       %s (%s)',
        'Rhythmus:     %s': 'Ritmo:        %s',
        'Rhythmus:     %s (%s)': 'Ritmo:        %s (%s)',
        'Wiedergabe:   %s': 'Reproducción: %s',
        'Wiedergabe:   %s (IsLoop %s)': 'Reproducción: %s (IsLoop %s)',
        'Pegel:        %+.1f dB': 'Nivel:        %+.1f dB',
        'Pegel:        %.1f dB → %.1f dB Spitze': 'Nivel:        %.1f dB → %.1f dB pico',
        'Pegel:        auf -1 dB Spitze normieren': 'Nivel:        normalizar a -1 dB pico',
        'Fade:         letzte %.1f s werden ausgeblendet': 'Fundido:      los últimos %.1f s se atenúan',
        'Fade:         letzte %.1f s der Aufnahme ausblenden': 'Fundido:      atenuar los últimos %.1f s',
        'Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)': 'Recorte:      cortado a %s (corte duro, 12 ms atenuados)',
        'Protokoll: %s': 'Registro: %s',
        'Sicherung: %s': 'Copia: %s',
        'Sicherung:  %s': 'Copia:  %s',
        'Schreibweise des Geräts: Patch%0*d': 'Ortografía del dispositivo: Patch%0*d',
        'Zu ändern: %d': 'A cambiar: %d',
        'Zuordnung liegt in: %s': 'La correspondencia está en: %s',
        'Betroffene Einträge (erste drei): %s': 'Entradas afectadas (las tres primeras): %s',
        'Keine Karte gefunden unter %s.': 'No se encontró tarjeta en %s.',
        '   ← wird umgewandelt': '← se convertirá',
        '   ← abweichend': '← diferente',
        '   ← über 10 min, Pedal nimmt sie nicht an': '← más de 10 min, la pedalera no lo acepta',
        'keine WAV': 'sin WAV',
        '  Das Fenster wäre leer. Es geht im Textmenü weiter.': 'La ventana quedaría vacía. El menú de texto sigue funcionando.',
        '  Für ein Fenster: Python von python.org installieren (bringt Tk 8.6).': 'Para una ventana: instala Python desde python.org (incluye Tk 8.6).',
        '  Tk %s kann auf diesem macOS keinen Text im Fenster zeichnen.': 'Tk %s no puede dibujar texto en una ventana en este macOS.',
        '  Hinweis: Fade konnte nicht eingerechnet werden.': 'Nota: el fundido no se pudo aplicar.',
        '  Hinweis: Pegel ließ sich nicht ändern (kein 16-Bit-WAV?).': 'Nota: el nivel no se pudo cambiar (¿WAV de 16 bits?).',
        '  Achtung: der WAV-Kopf ist weiterhin ungewöhnlich.': 'Atención: la cabecera WAV sigue siendo inusual.',
        '  Hinweis: Die Vorlage hatte kein Feld IsLoop - es wurde ergänzt.': 'Nota: la plantilla no tenía el campo IsLoop - se añadió.',
        '  Hinweis: Die Vorlage hatte kein Feld StopMode - es wurde ergänzt.': 'Nota: la plantilla no tenía el campo StopMode - se añadió.',
        '  Hinweis: Die Vorlage hatte kein Feld RhythmType - es wurde ergänzt.': 'Nota: la plantilla no tenía el campo RhythmType - se añadió.',
        '  Angehalten.': 'Detenido.',
        '  Das ist kein Fehler des Werkzeugs - die Karte oder': 'No es un error del programa — la tarjeta o',
        '  ihr Einhängepunkt ist beschädigt. Erste Schritte:': 'su punto de montaje está dañado. Primeros pasos:',
        '    dmesg | tail -20            (Meldungen des Systems)': 'dmesg | tail -20            (mensajes del sistema)',
        '    sudo umount <laufwerk>      (aushängen)': 'sudo umount <dispositivo>   (desmontar)',
        '    sudo fsck.vfat -n <geraet>  (FAT32, nur prüfen)': 'sudo fsck.vfat -n <dispos>  (FAT32, solo comprobar)',
        '    sudo exfatfsck <geraet>     (exFAT, nur prüfen)': 'sudo exfatfsck <dispos>     (exFAT, solo comprobar)',
        '    oder die Karte im JamMan einschalten - das Pedal': 'o pon la tarjeta en el JamMan y enciéndelo —',
        '    repariert seine Karte beim Einschalten.': 'la pedalera repara su tarjeta al arrancar.',
        'Erst eine Karte wählen.': 'Elige primero una tarjeta.',
        "Speicherkarte des JamMan Solo XT": "Tarjeta de memoria del JamMan Solo XT",
        "keine Karte gefunden": "no se encontró tarjeta",
        "Ordner": "Carpeta", "Name": "Nombre", "Dauer": "Duración",
        "Stoppart": "Parada", "Wdg.": "Reproducción", "Rhythmus": "Ritmo",
        "Wiedergabe": "Reproducción",
        "Patch markieren — dann hier klicken zum Springen":
            "Elige un patch — luego haz clic aquí para ir",
        "kein Patch gewählt": "ningún patch elegido",
        "automatisch weiter": "continuar automáticamente",
        "beim Start sichern": "copiar al iniciar",
        "Platz": "Posición",
        "nächster": "siguiente",
        "vormerken": "preparar",
        "vorgemerkt %d": "preparado %d",
        "Einstellungen:": "Ajustes:",
        "wie Vorlage": "como plantilla",
        "Fade am Ende": "Fundido al final",
        "Karte wählen": "Elegir tarjeta",
        "Datei aufspielen": "Importar archivo",
        "Alles sichern": "Copiar todo",
        "Pegel zurückrechnen": "Deshacer nivel",
        "Neu laden": "Recargar",
        "Anhören": "Escuchar",
        "Stopp": "Parar",
        "Umbenennen": "Renombrar",
        "Platz ändern": "Cambiar posición",
        "Sicherung zurückholen": "Restaurar copia",
        "Einstellungen anwenden": "Aplicar ajustes",
        "▲ nach oben": "▲ subir",
        "▼ nach unten": "▼ bajar",
        "Tauschen": "Intercambiar",
        "Datei teilen": "Dividir archivo",
        "Alle normieren": "Normalizar todo",
        "Löschen": "Borrar",
        "Karte auswerfen": "Expulsar tarjeta",
        "Pegel": "Nivel",
        "auf −1 dB Spitze normieren": "normalizar a −1 dB pico",
        "Teile": "Partes",
        "an Pausen schneiden": "cortar en pausas",
        "Anhebung auf 0 dB begrenzen": "limitar subida a 0 dB",
        "Doppelklick spielt · Ziehen ordnet um · ⌘ für zwei Markierungen, dann Tauschen · Esc hält an.":
            "Doble clic reproduce · arrastrar reordena · ⌘ para dos selecciones, luego intercambiar · Esc detiene.",
        "vorgemerkt: Platz %d": "preparado: posición %d",
        "Ende der Liste erreicht": "fin de la lista",
        "Erst einen Patch in der Liste wählen.": "Elige primero un patch de la lista.",
        "Bitte eine Platznummer eintragen.": "Introduce un número de posición.",
        "Platz %d gibt es auf dieser Karte nicht.": "La posición %d no existe en esta tarjeta.",
        "MP3 und M4A brauchen ffmpeg -": "MP3 y M4A necesitan ffmpeg -",
        "Anleitung": "Instrucciones",
        "WAV geht auch ohne": "WAV funciona sin él",
        "Schließen": "Cerrar",
        "Text kopieren": "Copiar texto",
        "Karte nicht lesbar": "Tarjeta ilegible",
        "Fehler": "Error",
        "Hinweis": "Nota",
        "Anleitung in die Zwischenablage kopiert": "Instrucciones copiadas",
        "Sicherung beim Start: %s": "Copia al iniciar: %s",
    },
    "zh": {
        'Spenden': '赞助',
        'Für dieses Werkzeug ist noch kein Spendenlink eingetragen.': '本工具尚未填写赞助链接。',
        'Er steht in jamman.py in der Zeile SPENDEN_LINK.': '请在 jamman.py 的 SPENDEN_LINK 一行填写。',
        '      Schnitt %d bei %s: %s': '      切分点 %d 位于 %s：%s',
        'keine Pause in der Nähe - rechnerisch geschnitten': '附近没有停顿 — 按计算位置切分',
        "Tk:            %s": "Tk:            %s",
        'Sicherung': '备份',
        'Aufspielen': '导入',
        'Der Name darf nicht leer sein.': '名称不能为空。',
        'Bitte 2 bis 60 Teile angeben.': '请输入 2 到 60 段。',
        'Die Datei ist nicht lesbar.': '该文件无法读取。',
        'Alle Patches normieren': '全部音色标准化',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nVorher wird die ganze Karte gesichert - das dauert bei\n900 MB ein bis zwei Minuten. Fortfahren?': '所有音色将统一响度：\n\n  目标 -18 dB 响度，峰值最高 -1 dB\n\n开始前会先备份整张卡 - 900 MB 大约需要\n一到两分钟。继续吗？',
        'Zum Tauschen genau ZWEI Patches markieren:\nersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘).': '交换需要正好选中两个音色：\n先点第一个，按住 Cmd (⌘) 再点第二个。',
        'Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern.': '上面全部是「同模板」- 没有可应用的设置。',
        'Erst einen Patch wählen.': '请先选择一个音色。',
        'Karte auswerfen? Danach kann sie abgezogen werden.': '弹出存储卡？之后即可拔出。',
        'Keine JamMan-Karte': '不是 JamMan 存储卡',
        'Datei zu lang': '文件太长',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3), oder das Feld leer lassen.': '请输入音量（dB，例如 -3），或留空。',
        'Bei Teile bitte eine ganze Zahl eintragen.': '请输入整数段数。',
        'Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3).': '请输入音量（dB，例如 -3）。',
        'Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.': '请输入结尾淡出的秒数。',
        'Pegel zurückgerechnet': '音量已撤销',
        'alle Patches normiert': '全部音色已标准化',
        'Fertig.': '完成。',
        'Tauschen:': '交换：',
        'Karte auswerfen, in den JamMan stecken — der Patch ist weg.': '弹出存储卡并插入 JamMan — 该音色已消失。',
        'Neue Reihenfolge:': '新的顺序：',
        'JamMan Solo XT — Speicherkarte verwalten': 'JamMan Solo XT — 管理存储卡',
        'Pythons auf diesem Rechner:': '本机上的 Python：',
        'Geprüfte Orte:': '已检查的位置：',
        'Laufwerke mit JamManSoloXT:': '含 JamManSoloXT 的驱动器：',
        'Trockenlauf — nichts geändert. Mit --schreiben ausführen.': '试运行 — 未做任何修改。请加 --schreiben 执行。',
        'Trockenlauf — nichts kopiert. Mit --schreiben ausführen.': '试运行 — 未复制任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts angelegt. Mit --schreiben ausführen.': '试运行 — 未创建任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.': '试运行 — 未写入任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.': '试运行 — 未恢复任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.': '试运行 — 未重命名任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts getauscht. Mit --schreiben ausführen.': '试运行 — 未交换任何内容。请加 --schreiben 执行。',
        'Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.': '试运行 — 未删除任何内容。请加 --schreiben 执行。',
        'Keine Patches auf der Karte.': '卡上没有音色。',
        'Suche musikalisch brauchbare Schnittstellen …': '正在寻找合适的切分点 …',
        'Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben': '文件头：      WAV 头已改写为简单 PCM',
        'Alle Ordner heißen schon so. Nichts zu tun.': '所有文件夹已经是这种写法。无需操作。',
        'Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:': '提示：这些不在卡上，将被新建：',
        'Nichts zu tun.': '无需操作。',
        '1  Patches auflisten': '1  列出音色',
        '2  Patch anhören': '2  试听音色',
        '3  Datei aufspielen': '3  导入文件',
        '4  Patch ändern (Stopp, Wiedergabe, Name)': '4  修改音色（停止、播放、名称）',
        '5  Patch verschieben': '5  移动音色',
        '6  Patch löschen': '6  删除音色',
        '7  Alles sichern': '7  全部备份',
        '8  Karte auswerfen': '8  弹出存储卡',
        '0  Beenden': '0  退出',
        'Angehalten.': '已停止。',
        'erste Zeile im Fenster:': '窗口中第一行：',
        'ACHTUNG: das Dateisystem liefert Lesefehler.': '注意：文件系统报读取错误。',
        'Das ist kein Fehler des Werkzeugs - die Karte oder': '这不是程序错误 — 存储卡或',
        'ihr Einhängepunkt ist beschädigt. Erste Schritte:': '它的挂载点已损坏。可以这样做：',
        'Ton:           afplay': '声音：        afplay',
        'Ton:           winsound (Windows-eigen)': '声音：        winsound（Windows 内置）',
        'Hinweis: Pegelprotokoll konnte nicht geschrieben werden.': '提示：音量记录无法写入。',
        'Sicherung beim Start: Karte unverändert, nichts zu tun.': '启动时备份：卡未变化，无需操作。',
        'Pause an der Naht wird herausgenommen': '接缝处的停顿将被去除',
        'Quelle:       %s': '来源：        %s',
        'Quelle: %s': '来源：%s',
        'Ziel:   %s': '目标：  %s',
        'Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB': '目标：%.1f dB 响度（RMS），峰值最高 %.1f dB',
        'Karte: %s': '存储卡：%s',
        'Name:         %s': '名称：        %s',
        'Dauer:        %s': '时长：        %s',
        'Dauer:  %s': '时长：%s',
        'Datei:  %s': '文件：  %s',
        'Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s': '格式：        %d Hz / %d 位 / %d 声道%s',
        'Fassung:       %s': '版本：        %s',
        'Python:        %s': 'Python：      %s',
        'System:        %s %s': '系统：        %s %s',
        'Tk-Modul:      %s': 'Tk 模块：     %s',
        'Schrift:       %s': '字体：        %s',
        'Kartenwurzeln: %s': '卡搜索位置：%s',
        'ffmpeg:        %s': 'ffmpeg：      %s',
        'gewählt:       %s': '已选择：      %s',
        'Ton:           %s': '声音：        %s',
        'Patches:       %d': '音色数：      %d',
        'Patches: %d': '音色数：%d',
        'Patches auf der Karte:    %d': '卡上的音色：     %d',
        'Patches in der Sicherung: %d': '备份中的音色：   %d',
        'Inhalt von %s:': '%s 的内容：',
        'Gesichert: %d Patches, %.0f MB.': '已备份：%d 个音色，%.0f MB。',
        'gesichert: %d Patches, %.0f MB': '已备份：%d 个音色，%.0f MB',
        'Fertig. Sicherung: %s': '完成。备份：%s',
        'Fertig: %s angelegt.': '完成：已创建 %s。',
        'Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.': '完成：重命名 %d 个文件夹，%d 个错误，%d 个音色可读。',
        'Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.': '完成：%d 个音色已标准化，其中 %d 个限制了峰值。',
        'Gelöscht. Sicherung: %s': '已删除。备份：%s',
        'Getauscht. Sicherung: %s': '已交换。备份：%s',
        'Geändert. Sicherung: %s': '已修改。备份：%s',
        'Verschoben. Sicherung: %s': '已移动。备份：%s',
        'Zurückgeholt. Der Zustand davor liegt in: %s': '已恢复。之前的状态位于：%s',
        'Zurückgerechnet: %d Patches. Sicherung davor: %s': '已撤销：%d 个音色。之前的备份：%s',
        'Vorher wird er gesichert nach: %s': '此前会先备份到：%s',
        'Wird gelöscht: %s  (%s)': '将被删除：%s  (%s)',
        'Neuer Patch:  Patch%s  (Name: %s)': '新音色：Patch%s  （名称：%s）',
        'Patch:        %s  (%s)': '音色：        %s  (%s)',
        'Patch:      %s  (%s)': '音色：      %s  (%s)',
        'Vorlage:      %s': '模板：        %s',
        'Teile:  %d × etwa %s': '段数：  %d × 约 %s',
        'Stopp:        %s': '停止方式：    %s',
        'Stopp:        %s (%s)': '停止方式：    %s (%s)',
        'Rhythmus:     %s': '节奏：        %s',
        'Rhythmus:     %s (%s)': '节奏：        %s (%s)',
        'Wiedergabe:   %s': '播放：        %s',
        'Wiedergabe:   %s (IsLoop %s)': '播放：        %s (IsLoop %s)',
        'Pegel:        %+.1f dB': '音量：        %+.1f dB',
        'Pegel:        %.1f dB → %.1f dB Spitze': '音量：        %.1f dB → %.1f dB 峰值',
        'Pegel:        auf -1 dB Spitze normieren': '音量：        标准化到 -1 dB 峰值',
        'Fade:         letzte %.1f s werden ausgeblendet': '淡出：        最后 %.1f 秒淡出',
        'Fade:         letzte %.1f s der Aufnahme ausblenden': '淡出：        淡出最后 %.1f 秒',
        'Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)': '截短：        切到 %s（硬切，12 毫秒淡出）',
        'Protokoll: %s': '记录：%s',
        'Sicherung: %s': '备份：%s',
        'Sicherung:  %s': '备份：  %s',
        'Schreibweise des Geräts: Patch%0*d': '设备的写法：Patch%0*d',
        'Zu ändern: %d': '需修改：%d',
        'Zuordnung liegt in: %s': '对应关系位于：%s',
        'Betroffene Einträge (erste drei): %s': '受影响的条目（前三个）：%s',
        'Keine Karte gefunden unter %s.': '在 %s 下未找到存储卡。',
        '   ← wird umgewandelt': '← 将被转换',
        '   ← abweichend': '← 格式不同',
        '   ← über 10 min, Pedal nimmt sie nicht an': '← 超过 10 分钟，踏板不接受',
        'keine WAV': '没有 WAV',
        '  Das Fenster wäre leer. Es geht im Textmenü weiter.': '窗口将是空白的。文本菜单仍可使用。',
        '  Für ein Fenster: Python von python.org installieren (bringt Tk 8.6).': '要使用窗口：请安装 python.org 的 Python（自带 Tk 8.6）。',
        '  Tk %s kann auf diesem macOS keinen Text im Fenster zeichnen.': '此 macOS 上 Tk %s 无法在窗口中绘制文字。',
        '  Hinweis: Fade konnte nicht eingerechnet werden.': '提示：淡出无法应用。',
        '  Hinweis: Pegel ließ sich nicht ändern (kein 16-Bit-WAV?).': '提示：音量无法更改（不是 16 位 WAV？）。',
        '  Achtung: der WAV-Kopf ist weiterhin ungewöhnlich.': '注意：WAV 头仍然异常。',
        '  Hinweis: Die Vorlage hatte kein Feld IsLoop - es wurde ergänzt.': '提示：模板缺少 IsLoop 字段 - 已补上。',
        '  Hinweis: Die Vorlage hatte kein Feld StopMode - es wurde ergänzt.': '提示：模板缺少 StopMode 字段 - 已补上。',
        '  Hinweis: Die Vorlage hatte kein Feld RhythmType - es wurde ergänzt.': '提示：模板缺少 RhythmType 字段 - 已补上。',
        '  Angehalten.': '已停止。',
        '  Das ist kein Fehler des Werkzeugs - die Karte oder': '这不是程序错误 — 存储卡或',
        '  ihr Einhängepunkt ist beschädigt. Erste Schritte:': '它的挂载点已损坏。可以这样做：',
        '    dmesg | tail -20            (Meldungen des Systems)': 'dmesg | tail -20            （系统消息）',
        '    sudo umount <laufwerk>      (aushängen)': 'sudo umount <设备>          （卸载）',
        '    sudo fsck.vfat -n <geraet>  (FAT32, nur prüfen)': 'sudo fsck.vfat -n <设备>    （FAT32，仅检查）',
        '    sudo exfatfsck <geraet>     (exFAT, nur prüfen)': 'sudo exfatfsck <设备>       （exFAT，仅检查）',
        '    oder die Karte im JamMan einschalten - das Pedal': '或者把卡插入 JamMan 并开机 —',
        '    repariert seine Karte beim Einschalten.': '踏板会在开机时修复它的卡。',
        'Erst eine Karte wählen.': '请先选择存储卡。',
        "Speicherkarte des JamMan Solo XT": "JamMan Solo XT 存储卡",
        "keine Karte gefunden": "未找到存储卡",
        "Ordner": "文件夹", "Name": "名称", "Dauer": "时长",
        "Stoppart": "停止方式", "Wdg.": "播放", "Rhythmus": "节奏",
        "Wiedergabe": "播放",
        "Patch markieren — dann hier klicken zum Springen":
            "先选择音色，再点击此处跳转",
        "kein Patch gewählt": "未选择音色",
        "automatisch weiter": "自动继续",
        "beim Start sichern": "启动时备份",
        "Platz": "位置",
        "nächster": "下一个",
        "vormerken": "预定",
        "vorgemerkt %d": "已预定 %d",
        "Einstellungen:": "设置：",
        "wie Vorlage": "同模板",
        "Fade am Ende": "结尾淡出",
        "Karte wählen": "选择存储卡",
        "Datei aufspielen": "导入文件",
        "Alles sichern": "全部备份",
        "Pegel zurückrechnen": "撤销音量",
        "Neu laden": "重新载入",
        "Anhören": "试听",
        "Stopp": "停止",
        "Umbenennen": "重命名",
        "Platz ändern": "更改位置",
        "Sicherung zurückholen": "恢复备份",
        "Einstellungen anwenden": "应用设置",
        "▲ nach oben": "▲ 上移",
        "▼ nach unten": "▼ 下移",
        "Tauschen": "交换",
        "Datei teilen": "分割文件",
        "Alle normieren": "全部标准化",
        "Löschen": "删除",
        "Karte auswerfen": "弹出存储卡",
        "Pegel": "音量",
        "auf −1 dB Spitze normieren": "标准化到 −1 dB 峰值",
        "Teile": "段数",
        "an Pausen schneiden": "在停顿处切分",
        "Anhebung auf 0 dB begrenzen": "提升限制在 0 dB",
        "Doppelklick spielt · Ziehen ordnet um · ⌘ für zwei Markierungen, dann Tauschen · Esc hält an.":
            "双击播放 · 拖动排序 · ⌘ 选两个后交换 · Esc 停止。",
        "vorgemerkt: Platz %d": "已预定：位置 %d",
        "Ende der Liste erreicht": "已到列表末尾",
        "Erst einen Patch in der Liste wählen.": "请先在列表中选择一个音色。",
        "Bitte eine Platznummer eintragen.": "请输入位置编号。",
        "Platz %d gibt es auf dieser Karte nicht.": "此卡上没有位置 %d。",
        "MP3 und M4A brauchen ffmpeg -": "MP3 和 M4A 需要 ffmpeg -",
        "Anleitung": "说明",
        "WAV geht auch ohne": "WAV 无需 ffmpeg",
        "Schließen": "关闭",
        "Text kopieren": "复制文本",
        "Karte nicht lesbar": "存储卡无法读取",
        "Fehler": "错误",
        "Hinweis": "提示",
        "Anleitung in die Zwischenablage kopiert": "说明已复制到剪贴板",
        "Sicherung beim Start: %s": "启动时备份：%s",
    },
}

SPRACHE = "de"

# Hinweis zur Übersetzung - erscheint im Fenster, wenn die Sprache nicht
# Deutsch oder Englisch ist. Diese Fassungen sind vorläufig; Korrekturen
# von Muttersprachlern sind ausdrücklich willkommen.
UEBERSETZUNGSHINWEIS = {
    "fr": "Traduction provisoire — corrections bienvenues.",
    "es": "Traducción provisional — se agradecen correcciones.",
    "zh": "翻译为初步版本，欢迎指正。",
}


def sprache_setzen(code):
    """Sprache wählen; unbekannte Angaben fallen auf Deutsch zurück."""
    global SPRACHE
    code = (code or "").strip().lower()[:2]
    SPRACHE = code if code in TEXTE else "de"


def sprache_aus_system():
    """Aus der Systemumgebung erraten (LANG, LC_ALL, Windows-Umgebung)."""
    for name in ("JAMMAN_SPRACHE", "LC_ALL", "LC_MESSAGES", "LANG"):
        wert = os.environ.get(name)
        if wert:
            return wert[:2]
    return "de"


def uebersetzt_mb(mb):
    """Hülle um tkinter.messagebox: übersetzt Titel und Text.

    So müssen die über 50 Dialogaufrufe im Fenster nicht einzeln angefasst
    werden - übersetzt wird beim Anzeigen.
    """
    class Huelle:
        def __init__(self, echt):
            self._echt = echt

        def __getattr__(self, name):
            funktion = getattr(self._echt, name)

            def aufruf(*args, **kwargs):
                neue = tuple(T(a) if isinstance(a, str) else a for a in args)
                return funktion(*neue, **kwargs)

            return aufruf

    return Huelle(mb)


def T(text):
    """Übersetzung holen: gewählte Sprache, sonst Englisch, sonst Deutsch.

    Einrückungen bleiben erhalten: die Katalogeinträge stehen ohne
    führende Leerzeichen, die Ausgabe behält ihre Spalten.
    """
    if SPRACHE == "de" or not isinstance(text, str):
        return text
    katalog = TEXTE.get(SPRACHE) or {}
    englisch = TEXTE.get("en") or {}
    if text in katalog:
        return katalog[text]
    if text in englisch:
        return englisch[text]
    einrueckung = text[:len(text) - len(text.lstrip())]
    rest = text.lstrip()
    if einrueckung and rest:
        if rest in katalog:
            return einrueckung + katalog[rest]
        if rest in englisch:
            return einrueckung + englisch[rest]
    return text


def freier_name(pfad):
    """Hängt -2, -3 … an, wenn der Name schon existiert.

    Zwei Änderungen in derselben Sekunde erzeugten sonst denselben
    Sicherungsordner - am 10.11.2026 geschehen, copytree brach ab.
    """
    if not os.path.exists(pfad):
        return pfad
    i = 2
    while os.path.exists("%s-%d" % (pfad, i)):
        i += 1
    return "%s-%d" % (pfad, i)


def sicher_ablegen(basis, patch_pfade, grund, wurzel=None):
    """Kopiert Patches vorher weg. Gibt den Zielordner zurück."""
    ordner = os.path.expanduser(wurzel or SICHERUNG_WURZEL)
    ziel = freier_name(os.path.join(
        ordner, "Sicherung_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        + "_" + grund))
    try:
        os.makedirs(ziel, exist_ok=True)
    except PermissionError:
        raise SystemExit("Fehler: in '%s' darf nicht geschrieben werden.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>" % ordner)
    for pfad in patch_pfade:
        shutil.copytree(pfad, os.path.join(ziel, os.path.basename(pfad)),
                        dirs_exist_ok=True)
    return ziel


def zuruecknehmen_kandidaten(ordner, nummer):
    """Sicherungen, die für diesen Platz eine Aufnahme enthalten,
    die jüngste zuerst. Namen wie Sicherung_2026-11-10_11-46-24_vor_fade."""
    treffer = []
    try:
        namen = sorted(os.listdir(ordner), reverse=True)
    except OSError:
        return treffer
    for name in namen:
        # "vor_zuruecknehmen" ist die Kopie des Zustands, der gerade ersetzt
        # wurde - kein Ziel zum Wiederherstellen.
        if name.endswith("vor_zuruecknehmen"):
            continue
        for form in ("Patch%02d" % nummer, "Patch%d" % nummer):
            k = os.path.join(ordner, name, form)
            if os.path.isdir(k) and os.path.exists(os.path.join(k, "PhraseA", "phrase.wav")):
                treffer.append(k)
                break
    return treffer


def befehl_alle_normieren(basis, schreiben, ziel_rms_db=-18.0, grenze_db=-1.0,
                          wurzel=None, fortschritt=None):
    """Alle Patches auf gleiche Lautheit bringen - für die Bühne.

    Normiert wird auf den Effektivwert (RMS), nicht auf die Spitze:
    gleich laute Spitzen bedeuten nicht gleich laute Loop. Die Spitze
    wird zusätzlich bei grenze_db gedeckelt, damit nichts übersteuert.
    """
    alle = patches(basis)
    if not alle:
        raise SystemExit("Keine Patches auf der Karte.")
    print(T("  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB")
          % (ziel_rms_db, grenze_db))
    print(T("  Patches: %d") % len(alle))
    if not schreiben:
        print()
        print(T("  %-10s %-24s %8s") % ("Ordner", "Name", "Lautheit jetzt"))
        for nummer, name, pfad in alle:
            w = phrase_wav(pfad)
            if not w:
                continue
            _, rms = pegel_info(w)
            print(T("  %-10s %-24s %+7.1f dB") % (name, patch_name(pfad)[:24], rms))
        print()
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return

    ziel = sicher_ablegen(basis, [pf for _, _, pf in alle], "vor_normieren", wurzel)
    print(T("  Sicherung: %s") % ziel)
    print()
    geaendert = 0
    gedeckelt = 0
    protokoll_eintraege = []
    for nummer, name, pfad in alle:
        w = phrase_wav(pfad)
        if not w:
            continue
        vorher = pegel_info(w)
        if vorher[0] is None:
            continue
        ergebnis = pegel_anpassen(w, ziel_rms_db=ziel_rms_db,
                                  begrenzen=True, grenze_db=grenze_db)
        if not ergebnis:
            continue
        nachher = pegel_info(w)
        if nachher[1] and vorher[1]:
            protokoll_eintraege.append({
                "ordner": name, "name": patch_name(pfad),
                "faktor": 10 ** ((nachher[1] - vorher[1]) / 20.0),
                "vorher": vorher[1], "nachher": nachher[1]})
        geaendert += 1
        if ergebnis["begrenzt"]:
            gedeckelt += 1
        if fortschritt:
            fortschritt("normiert: %s (%d/%d)" % (patch_name(pfad)[:24],
                                                 geaendert, len(alle)))
        print(T("  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s")
              % (name, patch_name(pfad)[:24], vorher[1], nachher[1],
                 nachher[0], "  [gedeckelt]" if ergebnis["begrenzt"] else ""))
    saeubere_karte(basis)
    schreibe_pegelprotokoll(basis, protokoll_eintraege, "alle_normieren", wurzel)
    print()
    print(T("  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.")
          % (geaendert, gedeckelt))


def befehl_zuruecknehmen_alle(basis, aus, schreiben, wurzel=None):
    """Alle Patches aus einer Sicherung zurückholen.

    Für den Fall, dass eine Sammelaktion (z. B. alle normieren) rückgängig
    gemacht werden soll.
    """
    quelle = os.path.expanduser(aus or "")
    if not os.path.isdir(quelle):
        raise SystemExit("Fehler: Sicherungsordner '%s' gibt es nicht." % quelle)
    vorhanden = sorted(d for d in os.listdir(quelle)
                       if re.fullmatch(r"Patch\d+", d))
    if not vorhanden:
        raise SystemExit("Fehler: in '%s' liegen keine Patch-Ordner." % quelle)
    auf_der_karte = [name for _, name, _ in patches(basis)]
    print(T("  Sicherung: %s") % quelle)
    print(T("  Patches in der Sicherung: %d") % len(vorhanden))
    print(T("  Patches auf der Karte:    %d") % len(auf_der_karte))
    fehlend = [x for x in vorhanden if x not in auf_der_karte]
    if fehlend:
        print(T("  Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:"))
        print(T("           %s") % ", ".join(fehlend))
    if not schreiben:
        print(T("  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen."))
        return
    ziel = sicher_ablegen(basis, [pf for _, _, pf in patches(basis)],
                          "vor_zuruecknehmen_alle", wurzel)
    for name in vorhanden:
        shutil.copytree(os.path.join(quelle, name), os.path.join(basis, name),
                        dirs_exist_ok=True)
    saeubere_karte(basis)
    print(T("  Zurückgeholt. Der Zustand davor liegt in: %s") % ziel)


def befehl_zuruecknehmen(basis, nummer, schreiben, wurzel=None, aus=None):
    """Holt eine frühere Fassung eines Patches aus der Sicherung zurück.

    Gedacht für den Fade: der verändert die Aufnahme, und rückgängig geht
    das nur über die Sicherung, die dabei angelegt wurde.
    """
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit("Fehler: Patch%d gibt es nicht." % nummer)
    ordner = os.path.expanduser(aus or wurzel or SICHERUNG_WURZEL)
    if not os.path.isdir(ordner):
        raise SystemExit("Fehler: Sicherungsordner '%s' gibt es nicht." % ordner)

    kandidaten = zuruecknehmen_kandidaten(ordner, nummer)
    if not kandidaten:
        raise SystemExit("Fehler: keine Sicherung mit einer Aufnahme für "
                         "Patch%d in '%s'.\n"
                         "  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade."
                         % (nummer, ordner))
    quelle = kandidaten[0]
    print(T("  Patch:      %s  (%s)") % (os.path.basename(alle[nummer]), patch_name(alle[nummer])))
    print(T("  Sicherung:  %s") % quelle)
    for datei in ("PhraseA/phrase.wav", "patch.xml", "PhraseA/phrase.xml"):
        q = os.path.join(quelle, datei)
        if os.path.exists(q):
            print(T("    %-22s %8.2f MB") % (datei, os.path.getsize(q) / 1024 / 1024))
    if not schreiben:
        print(T("  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen."))
        return
    ziel = sicher_ablegen(basis, [alle[nummer]], "vor_zuruecknehmen", wurzel)
    for datei in ("PhraseA/phrase.wav", "patch.xml", "PhraseA/phrase.xml"):
        q = os.path.join(quelle, datei)
        z = os.path.join(alle[nummer], datei)
        if os.path.exists(q):
            os.makedirs(os.path.dirname(z), exist_ok=True)
            shutil.copy2(q, z)
    saeubere_karte(basis)
    print(T("  Zurückgeholt. Der Zustand davor liegt in: %s") % ziel)


def befehl_platzieren(basis, von, auf, schreiben, wurzel=None):
    """Einen Patch auf eine bestimmte Platznummer legen.

    Beispiel: Patch200 soll Patch28 werden. Der Platz muss frei sein;
    ist er belegt, hilft "tauschen" weiter.
    """
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if von not in alle:
        raise SystemExit("Fehler: Patch%d gibt es nicht." % von)
    if auf in alle and auf != von:
        raise SystemExit("Fehler: Platz %d ist belegt (%s).\n"
                         "  Zum Tauschen:  tauschen --patch %d --mit %d"
                         % (auf, patch_name(alle[auf]), von, auf))
    pfad = alle[von]
    alter_name = os.path.basename(pfad)
    # Stellenzahl: zweistellig bis 99, dreistellig darunter nicht auffüllen
    neuer_name = "Patch%02d" % auf if auf < 100 else "Patch%d" % auf
    print(T("  %s  (%s)") % (alter_name, patch_name(pfad)))
    print(T("  →  %s") % neuer_name)
    if not schreiben:
        print(T("  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen."))
        return
    ziel = sicherung_der_namen(basis, "vor_platzieren", wurzel)
    os.rename(pfad, os.path.join(basis, neuer_name))
    saeubere_karte(basis)
    print(T("  Fertig. Sicherung: %s") % ziel)


def befehl_tauschen(basis, a, b, schreiben, wurzel=None, kopie=False):
    """Zwei Patches tauschen die Plätze. Der Inhalt bleibt, wo er ist -
    nur die Ordnernamen wechseln. Alle anderen bleiben unberührt."""
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if a not in alle or b not in alle:
        fehlend = a if a not in alle else b
        raise SystemExit("Fehler: Patch%d gibt es nicht." % fehlend)
    if a == b:
        print(T("  Nichts zu tun."))
        return
    pfad_a, pfad_b = alle[a], alle[b]
    name_a, name_b = os.path.basename(pfad_a), os.path.basename(pfad_b)
    if len(name_a) != len(name_b):
        print(T("  Achtung: %s und %s haben unterschiedliche Stellenzahl.") % (name_a, name_b))
        print(T("           Der JamMan sortiert nach Namen - der Patch landet dann"))
        print(T("           auf dem Platz, den sein neuer Name angibt."))
    print(T("  Tauschen:"))
    print(T("    Platz %-4s %-24s  →  Platz %s") % (a, patch_name(pfad_a), b))
    print(T("    Platz %-4s %-24s  →  Platz %s") % (b, patch_name(pfad_b), a))
    if not schreiben:
        print(T("  Trockenlauf — nichts getauscht. Mit --schreiben ausführen."))
        return

    ziel = sicherung_der_namen(basis, "vor_tauschen", wurzel)
    zwischen = os.path.join(basis, "tmp_tausch_%d" % a)
    os.rename(pfad_a, zwischen)
    os.rename(pfad_b, os.path.join(basis, name_a))
    os.rename(zwischen, os.path.join(basis, name_b))
    saeubere_karte(basis)
    print(T("  Getauscht. Sicherung: %s") % ziel)


def befehl_loeschen(basis, nummer, schreiben, wurzel=None):
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit("Fehler: Patch%d gibt es nicht." % nummer)
    pfad = alle[nummer]
    print(T("  Wird gelöscht: %s  (%s)") % (os.path.basename(pfad), patch_name(pfad)))
    print(T("  Vorher wird er gesichert nach: %s") % os.path.expanduser(wurzel or SICHERUNG_WURZEL))
    if not schreiben:
        print(T("  Trockenlauf — nichts gelöscht. Mit --schreiben ausführen."))
        return
    ziel = sicher_ablegen(basis, [pfad], "vor_loeschen", wurzel)
    shutil.rmtree(pfad)
    saeubere_karte(basis)
    print(T("  Gelöscht. Sicherung: %s") % ziel)
    print(T("  Karte auswerfen, in den JamMan stecken — der Patch ist weg."))


def sicherung_der_namen(basis, grund, wurzel=None):
    """Schreibt eine Liste Ordner → Patchname → Kennung weg.

    Beim reinen Umsortieren wird kein Inhalt verändert, nur umbenannt.
    Eine Kopie aller WAV-Dateien (867 MB) wäre dafür unverhältnismäßig;
    die Zuordnung genügt, um jeden Schritt rückgängig zu machen.
    """
    ordner = os.path.expanduser(wurzel or SICHERUNG_WURZEL)
    try:
        os.makedirs(ordner, exist_ok=True)
    except PermissionError:
        raise SystemExit("Fehler: in '%s' darf nicht geschrieben werden.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>" % ordner)
    name = "Umbenennung_%s_%s.txt" % (datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), grund)
    ziel = freier_name(os.path.join(ordner, name))
    try:
        f = open(ziel, "w", encoding="utf-8")
    except OSError:
        raise SystemExit("Fehler: '%s' lässt sich nicht schreiben.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>" % ziel)
    with f:
        f.write("Reihenfolge vor der Änderung (%s)\n" % datetime.now().isoformat(timespec="seconds"))
        f.write("Ordner\tName\tKennung\n")
        for nummer, ordnername, pfad in patches(basis):
            kennung = ""
            try:
                txt = open(os.path.join(pfad, "patch.xml"), encoding="utf-8",
                           errors="replace").read()
                m = re.search(r"<ID>([^<]*)", txt)
                kennung = m.group(1) if m else ""
            except OSError:
                pass
            f.write("%s\t%s\t%s\n" % (ordnername, patch_name(pfad), kennung))
    return ziel


def befehl_verschieben(basis, von, nach, schreiben, wurzel=None, kopie=True):
    """Patch 'von' an die Stelle 'nach' schieben; die anderen rücken nach."""
    reihe = [pf for _, _, pf in patches(basis)]
    namen = [os.path.basename(p) for p in reihe]
    if not (1 <= von <= len(reihe)) or not (1 <= nach <= len(reihe)):
        raise SystemExit("Fehler: Nummern müssen zwischen 1 und %d liegen." % len(reihe))
    if von == nach:
        print(T("  Nichts zu tun."))
        return
    bewegt = reihe.pop(von - 1)
    reihe.insert(nach - 1, bewegt)
    print(T("  Neue Reihenfolge:"))
    for i, pf in enumerate(reihe, start=1):
        alt = os.path.basename(pf)
        print(T("    Platz %d:  %-10s  %s%s") % (
            i, alt, patch_name(pf), "   ← wird verschoben" if pf == bewegt else ""))
    if not schreiben:
        print(T("  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen."))
        return

    # Die vorhandenen Ordnernamen bleiben erhalten - nur ihre Zuordnung
    # aendert sich. Am 26.10.2026 hat eine berechnete Breite aus einem
    # einzelnen "Patch200" alle Namen auf drei Stellen gebracht
    # (Patch001 ...). Der JamMan erkannte danach keinen Patch mehr.
    namen = sorted(os.path.basename(p) for p in reihe)
    if kopie:
        ziel = sicher_ablegen(basis, reihe, "vor_verschieben", wurzel)
    else:
        ziel = sicherung_der_namen(basis, "vor_verschieben", wurzel)

    for i, pf in enumerate(reihe, start=1):
        os.rename(pf, os.path.join(basis, "tmp_%03d" % i))
    for i, name in enumerate(namen, start=1):
        os.rename(os.path.join(basis, "tmp_%03d" % i), os.path.join(basis, name))
    saeubere_karte(basis)
    print(T("  Verschoben. Sicherung: %s") % ziel)



# ------------------------------------------------------- Menü ohne Fenster

def menu(karte):
    """Textmenü im Terminal. Braucht KEIN tkinter.

    Anlass 26.10.2026: Auf einem MacBook Pro von 2012 (macOS 15.8) bringt
    das mit Xcode gelieferte Python 3.9 das alte Tk 8.5.9 mit und stirbt
    beim Öffnen eines Fensters (Tcl_Panic in TkpInit, SIGABRT). Dieses Menü
    läuft dort ohne Einschränkung.
    """
    print()
    print(T("  JamMan Solo XT — Speicherkarte verwalten"))
    while True:
        basis = None
        try:
            basis = karte_pruefen(karte)
        except SystemExit:
            print(T("  Keine Karte gefunden unter %s.") % karte)
            neu = input("  Anderer Pfad (leer = beenden): ").strip()
            if not neu:
                return
            karte = neu
            continue

        print()
        print(T("  Karte: %s") % karte)
        print(T("  1  Patches auflisten"))
        print(T("  2  Patch anhören"))
        print(T("  3  Datei aufspielen"))
        print(T("  4  Patch ändern (Stopp, Wiedergabe, Name)"))
        print(T("  5  Patch verschieben"))
        print(T("  6  Patch löschen"))
        print(T("  7  Alles sichern"))
        print(T("  8  Karte auswerfen"))
        print(T("  0  Beenden"))
        wahl = input("  Auswahl: ").strip()

        try:
            if wahl == "0":
                return
            elif wahl == "1":
                befehl_liste(basis)
            elif wahl == "2":
                befehl_abspielen(basis, int(input("  Patch-Nummer: ").strip()))
            elif wahl == "3":
                datei = input("  Pfad zur Audiodatei: ").strip().strip("'")
                name = input("  Name (leer = Dateiname): ").strip() or None
                st = input("  Stopp (fade/instant, leer = wie Vorlage): ").strip() or None
                wg = input("  Wiedergabe (loop/single, leer = wie Vorlage): ").strip() or None
                if input("  Wirklich schreiben? (j/n): ").strip().lower() == "j":
                    befehl_aufspielen(basis, datei, name, True, st, None, wg)
            elif wahl == "4":
                nr = int(input("  Patch-Nummer: ").strip())
                st = input("  Stopp (fade/instant, leer = lassen): ").strip() or None
                wg = input("  Wiedergabe (loop/single, leer = lassen): ").strip() or None
                nm = input("  Neuer Name (leer = lassen): ").strip() or None
                if input("  Wirklich schreiben? (j/n): ").strip().lower() == "j":
                    befehl_aendern(basis, nr, True, st, None, wg, nm)
            elif wahl == "5":
                von = int(input("  Welcher Patch: ").strip())
                nach = int(input("  An welche Stelle: ").strip())
                if input("  Wirklich verschieben? (j/n): ").strip().lower() == "j":
                    befehl_verschieben(basis, von, nach, True)
            elif wahl == "6":
                nr = int(input("  Welcher Patch: ").strip())
                if input("  Wirklich löschen? (j/n): ").strip().lower() == "j":
                    befehl_loeschen(basis, nr, True)
            elif wahl == "7":
                ziel = input("  Zielordner (leer = ~/JamMan-Sicherung): ").strip() or "~/JamMan-Sicherung"
                befehl_sichern(basis, ziel, True)
            elif wahl == "8":
                befehl_auswerfen(karte)
                return
        except (ValueError, SystemExit) as e:
            print(T("  %s") % (str(e) or "Eingabe nicht verstanden."))
        except KeyboardInterrupt:
            print()
            return


# --------------------------------------------------------------------- GUI

# Farben ausdrücklich setzen. Ohne sie ist im dunklen Erscheinungsbild des
# Systems die Schrift der Liste unsichtbar (dunkel auf dunkel) - genau das
# war am 10.11.2026 zu sehen: Die Patches waren da, man konnte sie nur
# nicht lesen.
FARBE_HINTERGRUND = "#1e1e1e"
FARBE_SCHRIFT = "#f2f2f2"
FARBE_LEISE = "#a0a0a0"
FARBE_LISTE = "#161616"
FARBE_AUSWAHL = "#2f6fb3"
FARBE_TON = "#5fd08a"


def selbsttest(pfad=None):
    """Sagt, was das Fenster sehen müsste. Für Fehlersuche am Telefon.

    Wichtig: tkinter darf hier NICHT zwingend gebraucht werden - sonst
    bricht der Test genau auf dem Rechner ab, auf dem man ihn braucht
    (so geschehen am 10.11.2026 mit Homebrew-Python 3.15 ohne Tk).
    """
    print(T("  System:        %s %s") % (sys.platform,
          os.uname().release if hasattr(os, "uname") else ""))
    print(T("  Python:        %s") % sys.executable)
    print(T("  Fassung:       %s") % sys.version.split()[0])
    try:
        import tkinter as tk
        print(T("  Tk:            %s") % tk.TkVersion)
        try:
            import tkinter
            print(T("  Tk-Modul:      %s") % tkinter.__file__)
        except Exception:
            pass
    except Exception as e:
        print(T("  Tk:            NICHT verfügbar — %s") % e)
        print(T("                 (Fenster geht mit diesem Python nicht;"))
        print(T("                  das Textmenü geht trotzdem: jamman.py menu)"))
    if WINDOWS:
        print(T("  Ton:           winsound (Windows-eigen)"))
    elif MAC:
        print(T("  Ton:           afplay"))
    else:
        programme = [x for x in ("aplay", "paplay", "mpv", "ffplay", "cvlc")
                     if shutil.which(x)]
        print(T("  Ton:           %s") % (", ".join(programme) or "KEINES gefunden"))
    print(T("  Kartenwurzeln: %s") % ", ".join(karten_wurzeln()))
    ff = werkzeug_pfad("ffmpeg")
    print(T("  ffmpeg:        %s") % (ff if ff else
          "fehlt - MP3/M4A lassen sich nicht umwandeln (nur WAV)"))
    print(T("  Schrift:       %s") % (schrift(12)[0],))
    print(T("  Pythons auf diesem Rechner:"))
    for kandidat in ("/usr/bin/python3", "/usr/local/bin/python3",
                     "/opt/homebrew/bin/python3",
                     "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"):
        if not os.path.exists(kandidat):
            continue
        r = subprocess.run([kandidat, "-c",
                            "import tkinter;print(tkinter.TkVersion)"],
                           capture_output=True, text=True)
        stand = ("Tk %s" % r.stdout.strip()) if r.returncode == 0 else "kein nutzbares Tk"
        print(T("    %-62s %s") % (kandidat, stand))

    kandidaten = karten_kandidaten()
    print(T("  Geprüfte Orte:"))
    for zeile in karten_diagnose():
        print(zeile)
    print(T("  Laufwerke mit JamManSoloXT:"))
    for p, anz, name in kandidaten:
        print(T("    %-24s %3d Patches  %s") % (name, anz, p))
    basis = karte_finden(pfad)
    print(T("  gewählt:       %s") % (basis or "keine"))
    if basis:
        alle = patches(basis)
        print(T("  Patches:       %d") % len(alle))
        if not alle:
            fehler = karte_lesefehler(basis)
            if fehler:
                print(T("  ACHTUNG: das Dateisystem liefert Lesefehler."))
                print(T("  Das ist kein Fehler des Werkzeugs - die Karte oder"))
                print(T("  ihr Einhängepunkt ist beschädigt. Erste Schritte:"))
                if not WINDOWS and not MAC:
                    print(T("    dmesg | tail -20            (Meldungen des Systems)"))
                    print(T("    sudo umount <laufwerk>      (aushängen)"))
                    print(T("    sudo fsck.vfat -n <geraet>  (FAT32, nur prüfen)"))
                    print(T("    sudo exfatfsck <geraet>     (exFAT, nur prüfen)"))
                print(T("    oder die Karte im JamMan einschalten - das Pedal"))
                print(T("    repariert seine Karte beim Einschalten."))
                print(T("  Betroffene Einträge (erste drei): %s") % ", ".join(fehler[:3]))
            print(T("  Inhalt von %s:") % basis)
            try:
                for name in sorted(os.listdir(basis))[:12]:
                    pp = os.path.join(basis, name)
                    try:
                        st = os.stat(pp)
                        art = ("Ordner" if stat.S_ISDIR(st.st_mode) else "Datei")
                        art += " %04o" % (st.st_mode & 0o7777)
                    except OSError as e2:
                        art = "stat-Fehler: %s" % e2.strerror
                    innen = "patch.xml" if os.path.exists(
                        os.path.join(pp, "patch.xml")) else "kein patch.xml"
                    print(T("    %-24r %s, %s") % (name, art, innen))
            except OSError as e:
                print(T("    nicht lesbar: %s") % e)
        else:
            for nummer, name, pfad in alle[:3]:
                print(T("    %-10s %s") % (name, patch_name(pfad)))
            print(T("  erste Zeile im Fenster:"))
            print(T("    %r") % ("  %-10s %-36s %8s" % (
                alle[0][1], patch_name(alle[0][2])[:36], "—")))



def karte_finden(pfad=None, streng=False):
    """Karte suchen: erst der genannte Pfad, dann das wahrscheinlichste
    Laufwerk (Bewertung siehe karten_kandidaten).

    streng=True: Ist ein Pfad genannt, wird NUR dieser benutzt. Weicht die
    Suche nicht aus, kann ein Tippfehler keine fremde Karte verändern.
    """
    kandidaten = []
    if pfad:
        try:
            return karte_pruefen(os.path.expanduser(pfad))
        except SystemExit:
            if streng:
                raise
        kandidaten.append(os.path.expanduser(pfad))
    kandidaten += [p for p, _, _ in karten_kandidaten()]
    if not MAC:
        kandidaten.append(standard_karte())
    for k in kandidaten:
        try:
            return karte_pruefen(k)
        except SystemExit:
            continue
    return None


def gui(karte):
    """Oberfläche. Der Ton läuft nebenher: kein Warten, Stopp jederzeit."""
    # Sicherung beim Start - vor dem Fenster, damit man den Fortschritt
    # im Terminal sieht und das Fenster nicht einfriert.
    if einstellung_lesen("start_sichern") == "ja":
        beim_start_sichern(karte or karte_finden())

    import tkinter as tk
    from tkinter import filedialog, messagebox as _mb, simpledialog
    messagebox = uebersetzt_mb(_mb)

    # Apples Python 3.9 bringt Tk 8.5 mit. Auf macOS 27 zeichnet es die
    # Beschriftungen und die Liste nicht - das Fenster ist leer, die
    # Knöpfe sind da. (Am 10.11.2026 nachgestellt.) Deshalb: nur 8.6+.
    if float(tk.TkVersion) < 8.6:
        print(T("  Tk %s kann auf diesem macOS keinen Text im Fenster zeichnen.") % tk.TkVersion)
        print(T("  Das Fenster wäre leer. Es geht im Textmenü weiter."))
        print(T("  Für ein Fenster: Python von python.org installieren (bringt Tk 8.6)."))
        print()
        menu(karte)
        return

    basis = karte_finden(karte, streng=True)

    fenster = tk.Tk()
    fenster.minsize(1000, 720)
    fenster.configure(bg=FARBE_HINTERGRUND)

    # Sprachwahl oben rechts. Ein Wechsel öffnet das Fenster neu, damit
    # alle Beschriftungen in der neuen Sprache erscheinen.
    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=14, pady=(8, 0))
    sprach_var = tk.StringVar(value=SPRACHEN.get(SPRACHE, "Deutsch"))

    def sprache_waehlen(gewaehlt):
        code = next((c for c, n in SPRACHEN.items() if n == gewaehlt), SPRACHE)
        einstellung_schreiben("sprache", code)
        sprache_setzen(code)
        fenster.destroy()
        gui(basis_aktuell)

    tk.OptionMenu(leiste, sprach_var, *SPRACHEN.values(),
                  command=sprache_waehlen).pack(side="right")
    tk.Label(leiste, text="Sprache / Language", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="right", padx=(0, 8))

    def spenden():
        if not SPENDEN_LINK:
            messagebox.showinfo(
                T("Spenden"),
                T("Für dieses Werkzeug ist noch kein Spendenlink eingetragen.")
                + "\n\n"
                + T("Er steht in jamman.py in der Zeile SPENDEN_LINK."))
            return
        try:
            import webbrowser
            webbrowser.open(SPENDEN_LINK)
        except Exception:
            messagebox.showinfo(T("Spenden"), SPENDEN_LINK)

    tk.Button(leiste, text=T("Spenden"), command=spenden).pack(side="right", padx=(0, 12))


    tk.Label(fenster, text=T("Speicherkarte des JamMan Solo XT"),
             font=schrift(15, fest=True, titel=True),
             bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT).pack(pady=(12, 2))

    if SPRACHE in UEBERSETZUNGSHINWEIS:
        tk.Label(fenster, text=UEBERSETZUNGSHINWEIS[SPRACHE],
                 bg=FARBE_HINTERGRUND, fg="#c8a24a",
                 font=schrift(11)).pack(pady=(2, 0))

    pfad_var = tk.StringVar(value=basis or T("keine Karte gefunden"))
    tk.Label(fenster, textvariable=pfad_var, bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack()

    def zeige_ffmpeg_anleitung():
        hilfe = tk.Toplevel(fenster)
        hilfe.title("MP3 und M4A: ffmpeg installieren")
        hilfe.configure(bg=FARBE_HINTERGRUND)
        hilfe.geometry("760x560")
        text = tk.Text(hilfe, wrap="word", font=schrift(12),
                       bg="#ffffff", fg="#000000", relief="flat",
                       padx=14, pady=12)
        text.insert("1.0", ffmpeg_anleitung())
        text.config(state="disabled")
        text.pack(fill="both", expand=True, padx=12, pady=12)

        def kopieren():
            fenster.clipboard_clear()
            fenster.clipboard_append(ffmpeg_anleitung())
            status_var.set("Anleitung in die Zwischenablage kopiert")

        knoepfe = tk.Frame(hilfe, bg=FARBE_HINTERGRUND)
        knoepfe.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(knoepfe, text=T("Text kopieren"), command=kopieren).pack(side="left")
        tk.Button(knoepfe, text=T("Schließen"), command=hilfe.destroy).pack(side="right")

    if ffmpeg_fehlt():
        hinweis = tk.Frame(fenster, bg="#3a3320")
        hinweis.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(hinweis, text=T("MP3 und M4A brauchen ffmpeg -"),
                 bg="#3a3320", fg="#f0d48a").pack(side="left", padx=(10, 4))
        tk.Button(hinweis, text=T("Anleitung"),
                  command=zeige_ffmpeg_anleitung).pack(side="left", pady=4)
        tk.Label(hinweis, text=T("WAV geht auch ohne"),
                 bg="#3a3320", fg="#a0a0a0").pack(side="left", padx=8)

    tk.Label(fenster, text="  %-10s %-28s %8s  %-8s %-7s %s" % (
          T("Ordner"), T("Name"), T("Dauer"), T("Stoppart"), T("Wdg."),
          T("Rhythmus")),
        font=schrift(12), bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
        anchor="w", justify="left").pack(fill="x", padx=14)

    rahmen = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=14, pady=(0, 6))
    # Listbox statt Zeichenfläche: Tk bringt für Listboxen eine eigene
    # Rad-Behandlung mit (tk::MouseWheel …), für Zeichenflächen nicht.
    # Mit der Zeichenfläche kam das Mausrad auf diesem Rechner nicht mehr
    # durch (am 10.11.2026 mehrfach bestätigt). Der Einfügestrich wird
    # stattdessen als markierte Zeile "hier ablegen" eingeblendet.
    ZEILE_H = 24
    liste = tk.Listbox(rahmen, font=schrift(13),
                       background="#ffffff", foreground="#000000",
                       selectbackground=FARBE_AUSWAHL, selectforeground="#ffffff",
                       highlightthickness=0, borderwidth=0,
                       activestyle="none", selectmode="extended",
                       exportselection=False)
    liste.pack(side="left", fill="both", expand=True)
    rollbalken = tk.Scrollbar(rahmen, command=lambda *a: liste.yview(*a))
    rollbalken.pack(side="right", fill="y")
    liste.config(yscrollcommand=rollbalken.set)

    # Wellenform zum Scrubben, unter der Liste
    spuren = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    spuren.pack(fill="x", padx=14, pady=(6, 0))
    leinwand = tk.Canvas(spuren, height=84, bg="#111111",
                         highlightthickness=0, borderwidth=0)
    leinwand.pack(fill="x")
    zeit_var = tk.StringVar(value=T("kein Patch gewählt"))
    zeile_unten = tk.Frame(spuren, bg=FARBE_HINTERGRUND)
    zeile_unten.pack(fill="x")
    tk.Label(zeile_unten, textvariable=zeit_var, bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE, anchor="w").pack(side="left", fill="x", expand=True)
    # Blätterknöpfe: falls das Mausrad vom System nicht durchkommt
    weiter_var = tk.BooleanVar(value=False)
    start_sicher_var = tk.BooleanVar(
        value=einstellung_lesen("start_sichern") == "ja")

    def start_sicherung_umschalten():
        einstellung_schreiben("start_sichern",
                              "ja" if start_sicher_var.get() else "nein")
        status_var.set("Sicherung beim Start: %s"
                       % ("ein" if start_sicher_var.get() else "aus"))

    tk.Checkbutton(zeile_unten, text=T("beim Start sichern"),
                   variable=start_sicher_var, command=start_sicherung_umschalten,
                   bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                   activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left", padx=(0, 14))
    tk.Checkbutton(zeile_unten, text=T("automatisch weiter"),
                   variable=weiter_var, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                   activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left", padx=(0, 14))
    tk.Label(zeile_unten, text=T("Platz"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(10, 2))
    platz_eingabe = tk.Entry(zeile_unten, width=4, justify="center",
                             bg="#ffffff", fg="#000000",
                             insertbackground="#000000",
                             relief="solid", borderwidth=1)
    platz_eingabe.pack(side="left")
    platz_eingabe.bind("<Return>", lambda e: gehe_zu())
    platz_eingabe.bind("<Escape>", lambda e: (platz_eingabe.delete(0, "end"),
                                              zustand.__setitem__("tippen", ""),
                                              status_var.set("")))
    tk.Button(zeile_unten, text=T("nächster"), width=9,
              command=lambda: gehe_zu()).pack(side="left", padx=2)
    # Windows zeichnet Knöpfe nativ und ignoriert highlightbackground.
    # Deshalb sitzt der Knopf in einem eigenen Rahmen, dessen Farbe den
    # vorgemerkten Zustand zeigt - das geht auf allen Systemen.
    rahmen_vormerken = tk.Frame(zeile_unten, bg=FARBE_HINTERGRUND)
    rahmen_vormerken.pack(side="left", padx=2)
    knopf_vormerken = tk.Button(rahmen_vormerken, text=T("vormerken"), width=10,
                                command=lambda: vormerken())
    knopf_vormerken.pack(padx=2, pady=2)
    ursprung_rahmen = (knopf_vormerken.cget("highlightthickness"),
                       knopf_vormerken.cget("highlightbackground"))
    tk.Button(zeile_unten, text="▲ 5", width=4,
              command=lambda: seite(-1)).pack(side="left", padx=2)
    tk.Button(zeile_unten, text="▼ 5", width=4,
              command=lambda: seite(1)).pack(side="left", padx=2)

    basis_aktuell = basis
    zustand = {"basis": basis, "nummern": [], "spieler": None,
               "temp": None, "start": 0.0, "dauer": 0.0,
               "welle": [], "marke": None, "abschnitte": [],
               "zeilen": [], "auswahl": [], "oben": 0,
               "ziehung": None, "strich": None, "auto": None}

    # ---------------- Liste: füllen, auswählen, Strich ----------------
    def liste_zeichnen():
        """Nur den Einfügestrich pflegen - die Zeilen macht die Listbox."""
        if zustand.get("marker") is not None:
            liste.itemconfig(zustand["marker"], background="#ffe9a8",
                             foreground="#000000")

    def liste_setzen(zeilen):
        liste.delete(0, "end")
        zustand["zeilen"] = zeilen
        zustand["nummern"] = [z.get("nummer") for z in zeilen]
        for zeile in zeilen:
            liste.insert("end", zeile["text"])
        zustand["marker"] = None
        liste.yview_moveto(0.0)

    def liste_auswahl():
        return tuple(liste.curselection())

    def liste_waehlen(i, mehrfach=False):
        if i is None or not (0 <= i < liste.size()):
            liste.selection_clear(0, "end")
        elif mehrfach:
            if i in liste.curselection():
                liste.selection_clear(i)
            else:
                liste.selection_set(i)
        else:
            liste.selection_clear(0, "end")
            liste.selection_set(i)

    def liste_zeigen(i):
        if 0 <= i < liste.size():
            liste.see(i)

    def liste_index(y):
        return liste.nearest(y)

    def liste_einfuege_index(y):
        return liste.nearest(y)

    def strich_setzen(pos):
        """Markierte Zeile "hier ablegen" an der gewünschten Stelle."""
        if pos == zustand.get("marker"):
            return
        strich_weg()
        if pos is None:
            return
        pos = max(0, min(liste.size(), pos))
        liste.insert(pos, "  ═══ hier ablegen ═══")
        zustand["marker"] = pos
        liste.itemconfig(pos, background="#ffe9a8", foreground="#000000",
                         selectbackground="#ffe9a8")
        liste.see(pos)

    def strich_weg():
        pos = zustand.get("marker")
        if pos is not None and 0 <= pos < liste.size():
            liste.delete(pos)
        zustand["marker"] = None

    def liste_rad(e):
        zustand["nutzer_scroll"] = time.time()
        liste.yview_scroll(-3 if getattr(e, "delta", 0) > 0 else 3, "units")

    def zeilhoehe():
        """Tatsächliche Zeilenhöhe der Liste (die Schrift gibt sie vor)."""
        b0 = liste.bbox(0)
        b1 = liste.bbox(1)
        if b0 and b1 and b1[1] > b0[1]:
            return b1[1] - b0[1]
        return ZEILE_H

    def sichtbare_zeilen():
        return max(1, int(liste.winfo_height() / zeilhoehe()))

    def seite(richtung):
        """Blätterknöpfe: fünf Zeilen je Druck.

        Eine ganze Seite war zu grob - die Liste sprang fast ans Ende
        (Rückmeldung vom 10.11.2026). Ganze Seiten gibt es über Bild auf/ab.
        """
        zustand["nutzer_scroll"] = time.time()
        liste.yview_scroll(richtung * 5, "units")

    def liste_taste(e):
        if e.keysym == "Prior":
            zustand["nutzer_scroll"] = time.time()
            liste.yview_scroll(-sichtbare_zeilen(), "units")
        elif e.keysym == "Next":
            zustand["nutzer_scroll"] = time.time()
            liste.yview_scroll(sichtbare_zeilen(), "units")
        elif e.keysym == "Home":
            liste.yview_moveto(0.0)
        elif e.keysym == "End":
            liste.yview_moveto(1.0)

    import atexit
    atexit.register(lambda: stoppen_ton(zustand.get("spieler")))
    status_var = tk.StringVar(value="")
    _status_setzen = status_var.set

    def _status_uebersetzt(text):
        return _status_setzen(T(text) if isinstance(text, str) else text)

    status_var.set = _status_uebersetzt

    def zeit_laeuft():
        """Läuft die Wiedergabe - aus der Zeit gerechnet.

        Nötig auf Windows: winsound gibt keinen Prozess zurück, dessen
        Zustand man abfragen könnte. Aber wir kennen Startzeit und Länge.
        """
        if not zustand.get("t0"):
            return False
        lauf = zustand["start"] + (time.time() - zustand["t0"])
        if zustand.get("abschnitte"):
            return lauf < zustand["abschnitte"][-1][2]
        if zustand.get("dauer"):
            return lauf < zustand["dauer"]
        return False

    def laeuft():
        p = zustand["spieler"]
        if p is None:
            return False
        if hasattr(p, "poll"):
            return p.poll() is None
        return zeit_laeuft()

    

    def stoppen(merker_weg=True):
        """Wiedergabe beenden. merker_weg=False lässt eine Vormerkung stehen
        (z. B. beim Springen in der Wellenform)."""
        if merker_weg:
            zeige_vormerken(None)
            zustand["vorgemerkt"] = None
        p = zustand["spieler"]
        if p is not None:
            stoppen_ton(p)
        zustand["spieler"] = None
        if zustand.get("temp"):
            try:
                os.unlink(zustand["temp"])
            except OSError:
                pass
            zustand["temp"] = None
        zustand["marke"] = None
        alt = zustand.get("beobachter")
        if alt:
            try:
                fenster.after_cancel(alt)
            except Exception:
                pass
            zustand["beobachter"] = None
        zeichne_welle()
        if knopf_stopp is not None:
            knopf_stopp.config(state="disabled")
        status_var.set("")

    def _auswahl_auf(nummer):
        """Markierung auf einen Patch setzen, ohne etwas zu starten."""
        nummern = zustand["nummern"]
        if nummer not in nummern:
            return
        i = nummern.index(nummer)
        liste.selection_clear(0, "end")
        liste.selection_set(i)
        if time.time() - zustand.get("nutzer_scroll", 0) > 5:
            liste.see(i)
        welle_laden()

    def naechster():
        """Den Patch nach dem gerade gespielten starten."""
        nummern = zustand["nummern"]
        jetzt = zustand.get("spielend")
        if not nummern or jetzt is None or jetzt not in nummern:
            return
        i = nummern.index(jetzt)
        if i + 1 >= len(nummern):
            status_var.set("Ende der Liste erreicht")
            return
        if zustand.get("marker") is not None:
            strich_weg()
        liste.selection_clear(0, "end")
        liste.selection_set(i + 1)
        liste.see(i + 1)
        welle_laden()
        abspielen()

    def plane_beobachten(ms=300):
        """Plant die Überwachung - und hebt eine alte auf.

        Ohne das liefen mehrere Beobachter gleichzeitig (nach einem Sprung
        während der Wiedergabe) und schrieben abwechselnd die Marke.
        """
        alt = zustand.get("beobachter")
        if alt:
            try:
                fenster.after_cancel(alt)
            except Exception:
                pass
        zustand["beobachter"] = fenster.after(ms, beobachten)

    def umschalten_auf_merker():
        """Am Ende des laufenden Patches auf die vorbereitete Kette wechseln."""
        datei = zustand.get("vorgemerkt_datei")
        abschnitte = zustand.get("vorgemerkt_abschnitte") or []
        nummern = zustand["nummern"]
        if not datei or not abschnitte:
            return
        zustand["vorgemerkt_datei"] = None
        zustand["vorgemerkt_abschnitte"] = []
        zustand["vorgemerkt"] = None
        zeige_vormerken(None)
        alt = zustand.get("spieler")
        if alt is not None:
            stoppen_ton(alt)
        if zustand.get("temp"):
            try:
                os.unlink(zustand["temp"])
            except OSError:
                pass
        zustand["spieler"] = abspielen_datei(datei)
        zustand["temp"] = datei
        zustand["abschnitte"] = abschnitte
        zustand["start"] = 0.0
        zustand["t0"] = time.time()
        zustand["spielend"] = abschnitte[0][0]
        zustand["marke"] = 0.0
        if abschnitte[0][0] in nummern:
            _auswahl_auf(abschnitte[0][0])
        zeichne_welle()
        plane_beobachten(120)

    def plane_umschalten():
        """Zeitpunkt für das Umschalten aus der laufenden Wiedergabe planen.

        Muss nach jedem Sprung neu laufen, weil dann eine andere Stelle
        läuft.
        """
        alt = zustand.get("umschalt_plan")
        if alt:
            try:
                fenster.after_cancel(alt)
            except Exception:
                pass
            zustand["umschalt_plan"] = None
        if zustand.get("vorgemerkt") is None:
            return
        jetzt, position = stelle_im_patch()
        rest = None
        if jetzt is not None and position is not None:
            if zustand.get("abschnitte"):
                for nr, von, bis, versatz in zustand["abschnitte"]:
                    if nr == jetzt and versatz <= position < versatz + (bis - von):
                        rest = (bis - von) - (position - versatz)
                        break
            elif zustand.get("dauer"):
                rest = zustand["dauer"] - position
        if rest is not None and rest > 0.3:
            zustand["umschalt_plan"] = fenster.after(
                max(0, int((rest - 0.1) * 1000)), umschalten_auf_merker)

    def zeige_vormerken(nummer=None):
        """Vorgemerkten Zustand am Knopf sichtbar machen.

        Auf macOS zeichnet Tk die Knopffläche nicht um, ein farbiger
        Rahmen (highlightbackground) geht aber.
        """
        if nummer is None:
            knopf_vormerken.config(text=T("vormerken"),
                                   highlightthickness=ursprung_rahmen[0],
                                   highlightbackground=ursprung_rahmen[1],
                                   relief="raised")
            rahmen_vormerken.config(bg=FARBE_HINTERGRUND)
        else:
            knopf_vormerken.config(text=T("vorgemerkt %d") % nummer,
                                   highlightthickness=3,
                                   highlightbackground="#e8b04b",
                                   highlightcolor="#e8b04b",
                                   relief="raised")
            rahmen_vormerken.config(bg="#e8b04b")

    def spiel_ueberwachen():
        """Nachsehen, ob noch etwas klingt - und die Vormerkung ausführen."""
        merker = zustand.get("vorgemerkt")
        if merker is not None and not laeuft():
            zustand["vorgemerkt"] = None
            gehe_zu(merker)
            return
        if zustand.get("spielend") is not None:
            fenster.after(400, spiel_ueberwachen)

    def beobachten():
        if not laeuft() and zustand.pop("weiter_gewuenscht", False):
            # Nach dem Ende der Kette beim letzten gespielten Patch neu
            # ansetzen - die Kette selbst war schon nahtlos.
            zustand["abschnitte"] = []
            naechster()
            return
        if laeuft():
            lauf = zustand["start"] + (time.time() - zustand.get("t0", time.time()))
            if zustand.get("abschnitte"):
                for nr, von, bis, versatz in zustand["abschnitte"]:
                    if von <= lauf < bis:
                        if zustand.get("vorgemerkt") == nr:
                            zustand["vorgemerkt"] = None
                            zeige_vormerken(None)
                        zustand["marke"] = max(0.0, min(zustand["dauer"],
                                                        versatz + (lauf - von)))
                        if zustand.get("spielend") != nr:
                            zustand["spielend"] = nr
                            _auswahl_auf(nr)      # lädt Welle und zeichnet Marke
                        else:
                            zeichne_welle(nur_marke=True)
                        break
                plane_beobachten(120)
                return
            if zustand["dauer"]:
                zustand["marke"] = min(zustand["dauer"], lauf)
                zeichne_welle(nur_marke=True)
            plane_beobachten(300)
        else:
            zustand["spieler"] = None
            if zustand.get("temp"):
                try:
                    os.unlink(zustand["temp"])
                except OSError:
                    pass
                zustand["temp"] = None
            if knopf_stopp is not None:
                knopf_stopp.config(state="disabled")
            merker = zustand.get("vorgemerkt")
            if merker is not None:
                zustand["vorgemerkt"] = None
                gehe_zu(merker)
                return
            if weiter_var.get() and zustand.get("spielend") is not None:
                zustand["weiter_gewuenscht"] = True
                plane_beobachten(30)
            else:
                status_var.set("")

    def neu_laden(behalte=True):
        # Auswahl merken: nach dem Neuladen soll derselbe Patch markiert
        # bleiben, sonst muss man nach jeder Änderung neu klicken.
        vorher = None
        i = liste_auswahl()
        if i and zustand["nummern"]:
            vorher = zustand["nummern"][i[0]]
        zeilen = []
        zustand["nummern"] = []
        if not zustand["basis"]:
            zustand["basis"] = karte_finden()
        if not zustand["basis"]:
            pfad_var.set("keine Karte gefunden")
            liste_setzen([{"text": "  Karte einlegen und dann oben links Karte waehlen."}])
            fenster.title("JamMan Solo XT — keine Karte")
            return
        pfad_var.set(zustand["basis"])
        for nummer, name, pfad in patches(zustand["basis"]):
            w = phrase_wav(pfad)
            stop, rhy, loop = patch_einstellungen(pfad)
            dauer = wav_info(w)[0] if w else 0
            marke = "  ← über 10 min" if dauer > MAX_LOOP_SEKUNDEN else ""
            zeilen.append({"text": "  %-10s %-28s %8s  %-8s %-7s %s%s" % (
                name, patch_name(pfad)[:28], sekunden(dauer) if w else "—",
                stop, loop, rhy, marke),
                "nummer": nummer})
            zustand["nummern"].append(nummer)
        liste_setzen(zeilen)
        if zustand["nummern"]:
            fenster.title(T("JamMan Solo XT") + " — %d" % len(zustand["nummern"]))
            if not zustand["nummern"] and zustand["basis"] and not zustand.get("lesefehler_gemeldet"):
                fehler = karte_lesefehler(zustand["basis"])
                if fehler:
                    zustand["lesefehler_gemeldet"] = True
                    messagebox.showerror(
                        "Karte nicht lesbar",
                        "Die Karte ist eingebunden, aber das Dateisystem liefert\n"
                        "Lesefehler:\n\n%s\n\n"
                        "Das liegt an der Karte oder am Einhängepunkt, nicht am\n"
                        "Werkzeug.\n\n"
                        "Erste Schritte:\n"
                        "  - Karte im JamMan einschalten (das Pedal prüft sie)\n"
                        "  - dmesg | tail -20\n"
                        "  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n"
                        "    bzw. sudo exfatfsck <gerät>"
                        % "\n".join(fehler[:4]))
            if behalte and vorher is not None and vorher in zustand["nummern"]:
                i = zustand["nummern"].index(vorher)
                liste_waehlen(i)
                liste_zeigen(i)
            # Spur mitziehen: nach Löschen/Umbenennen darf keine alte
            # Wellenform stehen bleiben (am 10.11.2026 gesehen).
            welle_laden()
        else:
            fenster.title("JamMan Solo XT — keine Patches")
            liste_setzen([{"text": "  Keine Patches im Ordner %s"
                                    % os.path.basename(zustand["basis"])}])
            zustand["welle"] = []
            zustand["dauer"] = 0.0
            zustand["marke"] = None
            zeichne_welle()
            zeit_var.set(T("kein Patch gewählt"))

    def zeichne_welle(nur_marke=False):
        """Wellenform zeichnen - oder nur die Marke verschieben.

        Die 900 Striche bei jeder Bewegung neu zu zeichnen war der Grund,
        warum das Fenster träge wurde (am 10.11.2026 mit Beachball).
        """
        breite = max(200, leinwand.winfo_width() or 900)
        hoehe = 84
        if nur_marke and zustand.get("marke_id") and zustand["dauer"]:
            x = breite * (zustand["marke"] / zustand["dauer"])
            leinwand.coords(zustand["marke_id"], x, 0, x, hoehe)
            return
        leinwand.delete("all")
        zustand["marke_id"] = None
        if not zustand["welle"]:
            leinwand.create_text(10, hoehe//2, anchor="w", fill=FARBE_LEISE,
                                 text=T("Patch markieren — dann hier klicken zum Springen"))
            return
        werte = zustand["welle"]
        schritt = breite / float(len(werte))
        mitte = hoehe // 2
        for i, wert in enumerate(werte):
            x = i * schritt
            h = max(1, int(wert * (hoehe/2 - 4)))
            leinwand.create_line(x, mitte - h, x, mitte + h, fill="#5a9fd4")
        leinwand.create_line(0, mitte, breite, mitte, fill="#333333")
        if zustand["marke"] is not None and zustand["dauer"]:
            x = breite * (zustand["marke"] / zustand["dauer"])
            zustand["marke_id"] = leinwand.create_line(x, 0, x, hoehe,
                                                       fill="#e05a5a", width=2)

    def welle_laden():
        nummer = gewaehlt()
        if nummer is not None and zustand.get("welle_fuer") == nummer and zustand["welle"]:
            zeichne_welle()
            return
        zustand["welle_fuer"] = nummer
        zustand["welle"] = []
        zustand["dauer"] = 0.0
        # Marke NICHT löschen: beim Weiterspielen wird sie gleich wieder
        # gesetzt, und ein Neuzeichnen soll sie nicht verschlucken.

        if not nummer:
            zeichne_welle()
            zeit_var.set(T("kein Patch gewählt"))
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        wav = phrase_wav(alle[nummer])
        if not wav:
            zeichne_welle()
            return
        werte, dauer, _ = wellenform(wav, 900)
        zustand["welle"] = werte
        zustand["dauer"] = dauer
        zeit_var.set("%s   •   %s   •   Klick in die Spur springt dorthin"
                     % (patch_name(alle[nummer]), sekunden(dauer)))
        zeichne_welle()

    def kette_bauen(start_nummer, position=0.0, cue=None, grenze=30 * 60):
        """Datei bauen: ab 'position' im Startpatch, dann (cue) und die Folgenden.

        Gibt (datei, abschnitte) zurück; abschnitte = [(plätze, von, bis, versatz)].
        """
        nummern = zustand["nummern"]
        if start_nummer not in nummern:
            return None, []
        i = nummern.index(start_nummer)
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        pfade = []
        versatz_start = 0.0
        if position > 0.01:
            rest = neue_tempdatei("jamman-rest-", ".wav")
            dauer_patch = wav_info(phrase_wav(alle[start_nummer]))[0]
            if not schneide_bereich(phrase_wav(alle[start_nummer]), rest,
                                    position, dauer_patch):
                return None, []
            pfade.append((start_nummer, rest))
            versatz_start = position
        else:
            pfade.append((start_nummer, phrase_wav(alle[start_nummer])))
        if cue is not None and cue in nummern and cue != start_nummer:
            # Vorgemerkter Patch zuerst, danach die, die NACH IHM kommen.
            j = nummern.index(cue)
            folge = [cue] + nummern[j+1:]
        else:
            folge = nummern[i+1:]
        gesamt = wav_info(pfade[0][1])[0]
        for nr in folge:
            pfad = phrase_wav(alle[nr])
            if not pfad:
                break
            d = wav_info(pfad)[0]
            if len(pfade) >= 2 and gesamt + d > grenze:
                break
            pfade.append((nr, pfad))
            gesamt += d
            if len(pfade) >= 8:
                break
        if len(pfade) < 2:
            return None, []
        datei = neue_tempdatei("jamman-kette-", ".wav")
        dauern, _ = verbinde_patches([pf for _, pf in pfade], datei)
        if not dauern:
            try:
                os.unlink(datei)
            except OSError:
                pass
            return None, []
        abschnitte = []
        lauf = 0.0
        for k, ((nr, _), d) in enumerate(zip(pfade, dauern)):
            abschnitte.append((nr, lauf, lauf + d, versatz_start if k == 0 else 0.0))
            lauf += d
        # Aufräumen der Ausschnittsdatei(en) - die Kette hat den Inhalt
        for nr, pf in pfade:
            if pf != phrase_wav(alle[nr]):
                try:
                    os.unlink(pf)
                except OSError:
                    pass
        return datei, abschnitte

    def stelle_im_patch():
        """Wie weit ist die Wiedergabe im KLINGENDEN Patch? (Sekunden)"""
        if not zustand.get("spieler") or not zustand.get("t0"):
            return None, None
        lauf = zustand["start"] + (time.time() - zustand["t0"])
        for nr, von, bis, versatz in zustand.get("abschnitte") or []:
            if von <= lauf < bis:
                return nr, versatz + (lauf - von)
        if zustand.get("dauer"):
            return zustand.get("spielend"), min(zustand["dauer"], lauf)
        return zustand.get("spielend"), 0.0

    def vormerken(nummer=None):
        """Einen Patch vor den nächsten setzen - er kommt, wenn der jetzige
        zu Ende ist (wie das Vormerken mit dem FS3X am Pedal)."""
        nummern = zustand["nummern"]
        if nummer is None:
            try:
                nummer = int(platz_eingabe.get().strip())
            except (ValueError, AttributeError):
                messagebox.showerror("Fehler", "Bitte eine Platznummer eintragen.")
                return
        if nummer not in nummern:
            messagebox.showerror("Fehler", "Platz %d gibt es nicht." % nummer)
            return
        zustand["vorgemerkt"] = nummer
        # Kette für den vorgemerkten Patch schon jetzt bauen: reine
        # Dateiarbeit, der Ton läuft ungestört weiter.
        datei, abschnitte = kette_bauen(nummer, 0.0, None)
        if datei:
            alte = zustand.get("vorgemerkt_datei")
            if alte:
                try:
                    os.unlink(alte)
                except OSError:
                    pass
            zustand["vorgemerkt_datei"] = datei
            zustand["vorgemerkt_abschnitte"] = abschnitte
        else:
            zustand["vorgemerkt_datei"] = None
            zustand["vorgemerkt_abschnitte"] = []
        # Bewusst KEIN Neuaufbau des Tonstroms: das verursachte einen
        # kurzen Aussetzer mitten im laufenden Patch (Rückmeldung vom
        # 10.11.2026). Die Vormerkung wird stattdessen am Ende des
        # laufenden Patches ausgeführt.
        status_var.set("vorgemerkt: Platz %d" % nummer)
        zeige_vormerken(nummer)
        # Zeitgesteuert umschalten: das Ende des laufenden Patches ist
        # bekannt, also lässt sich der Wechsel vorher planen. Auf das Ende
        # des Tonprozesses zu warten kostete hörbar Zeit (0,7 s gemessen).
        plane_umschalten()
        fenster.after(400, spiel_ueberwachen)

    def gehe_zu(nummer=None):
        """Einen bestimmten Platz auswählen und abspielen (wie am Pedal)."""
        nummern = zustand["nummern"]
        if nummer is None:
            roh = platz_eingabe.get().strip()
            if not roh:
                # Ohne Nummer: den nächsten Patch - wie "weiter" am Pedal
                jetzt = zustand.get("spielend")
                if jetzt not in nummern:
                    auswahl = liste_auswahl()
                    jetzt = nummern[auswahl[0]] if auswahl else None
                if jetzt is None or jetzt not in nummern:
                    return
                i = nummern.index(jetzt)
                if i + 1 >= len(nummern):
                    status_var.set("Ende der Liste erreicht")
                    return
                nummer = nummern[i + 1]
            else:
                try:
                    nummer = int(roh)
                except ValueError:
                    messagebox.showerror("Fehler", "Bitte eine Platznummer eintragen.")
                    return
        if nummer not in nummern:
            messagebox.showerror("Fehler",
                                 "Platz %d gibt es auf dieser Karte nicht." % nummer)
            return
        i = nummern.index(nummer)
        if zustand.get("marker") is not None:
            strich_weg()
        liste.selection_clear(0, "end")
        liste.selection_set(i)
        liste.see(i)
        welle_laden()
        abspielen()

    def tippen(e):
        """Ziffern in der Liste tippen und mit Enter zum Platz springen.

        Das ist der Weg wie am Pedal: Nummer eintippen, fertig. Er hängt
        nicht am Fokus eines Eingabefeldes.
        """
        if getattr(e.widget, "winfo_class", lambda: "")() == "Entry":
            return None            # dort tippt Tk selbst
        zeichen = getattr(e, "char", "") or ""
        if zeichen.isdigit():
            zustand["tippen"] = (zustand.get("tippen") or "") + zeichen
            try:
                platz_eingabe.delete(0, "end")
                platz_eingabe.insert(0, zustand["tippen"])
            except Exception:
                pass
            status_var.set("Platz %s  (Eingabetaste springt hin, Esc löscht)"
                           % zustand["tippen"])
            return "break"
        if e.keysym in ("Return", "KP_Enter"):
            roh = zustand.get("tippen")
            zustand["tippen"] = ""
            if roh:
                gehe_zu(int(roh))
            return "break"
        if e.keysym == "Escape":
            zustand["tippen"] = ""
            try:
                platz_eingabe.delete(0, "end")
            except Exception:
                pass
            status_var.set("")
            return "break"
        return None

    def springe(event):
        """Klick in die Spur: ab dieser Stelle abspielen."""
        if not zustand["welle"] or not zustand["dauer"]:
            return
        breite = max(1, leinwand.winfo_width())
        anteil = min(1.0, max(0.0, event.x / float(breite)))
        ziel = anteil * zustand["dauer"]
        nummer = gewaehlt()
        if not nummer:
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        wav = phrase_wav(alle[nummer])
        if laeuft():
            stoppen(merker_weg=False)
        zustand["abschnitte"] = []
        griff = temp = None
        start = 0.0             # Zeitbasis: Anfang der Kette bzw. des Ausschnitts
        if weiter_var.get():
            # Auch beim Springen nahtlos weiter: Ausschnitt ab der
            # Klickstelle plus die folgenden Patches in EINER Datei.
            # Sonst entsteht am Ende des Ausschnitts wieder eine Pause
            # (am 10.11.2026 bei einer Karte mit langen Teilen gehört).
            nummern = zustand["nummern"]
            i = nummern.index(nummer) if nummer in nummern else -1
            rest = neue_tempdatei("jamman-rest-", ".wav")
            dauer_patch = wav_info(wav)[0]
            if schneide_bereich(wav, rest, ziel, dauer_patch):
                folge = [(nummer, rest)]
                gesamt = dauer_patch - ziel
                for nr in nummern[i+1:]:
                    pfad2 = phrase_wav(alle[nr])
                    if not pfad2:
                        break
                    d2 = wav_info(pfad2)[0]
                    if len(folge) >= 2 and gesamt + d2 > 30 * 60:
                        break
                    folge.append((nr, pfad2))
                    gesamt += d2
                    if len(folge) >= 8:
                        break
                kette = neue_tempdatei("jamman-kette-", ".wav")
                dauern, _ = verbinde_patches([pf for _, pf in folge], kette)
                try:
                    os.unlink(rest)
                except OSError:
                    pass
                if dauern:
                    griff = abspielen_datei(kette)
                    temp = kette
                    lauf = 0.0
                    for kk, ((nr, _), d) in enumerate(zip(folge, dauern)):
                        versatz = ziel if kk == 0 else 0.0
                        zustand["abschnitte"].append((nr, lauf, lauf + d, versatz))
                        lauf += d
        if griff is None:
            zustand["abschnitte"] = []
            griff, temp, start = abspielen_ab(wav, ziel)
        zustand["spieler"] = griff
        zustand["temp"] = temp
        zustand["start"] = start
        zustand["spielend"] = nummer
        zustand["t0"] = time.time()
        zustand["marke"] = ziel
        zeichne_welle()
        if knopf_stopp is not None:
            knopf_stopp.config(state="normal")
        status_var.set("spielt: %s ab %s" % (patch_name(alle[nummer]), sekunden(ziel)))
        plane_umschalten()
        beobachten()

    def karte_waehlen():
        # Dort beginnen, wo Wechselmedien liegen - sonst landet der Dialog
        # im Benutzerordner und man sucht die Karte selbst.
        start = ""
        for ort in karten_wurzeln():
            if os.path.isdir(ort) and os.listdir(ort):
                start = ort
                break
        p = filedialog.askdirectory(title="Speicherkarte des JamMan wählen",
                                    initialdir=start or None)
        if not p:
            return
        try:
            zustand["basis"] = karte_pruefen(p)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror("Keine JamMan-Karte", str(e))

    def wahl_wert(var):
        """Angezeigten Text zurück auf den deutschen Wert führen."""
        wert = var.get()
        for deutsch in ("wie Vorlage", "fade", "instant", "finish",
                        "silence", "kick", "loop", "single"):
            if T(deutsch) == wert or deutsch == wert:
                return deutsch
        return wert

    def gewaehlt():
        i = liste_auswahl()
        return zustand["nummern"][i[0]] if i else None

    def abspielen():
        # Läuft schon etwas, wird es beendet und der gewählte Patch
        # gestartet. Die Beschriftung bleibt "Anhören" - sonst stünden
        # zwei Knöpfe mit "Stopp" nebeneinander (am 10.11.2026 gesehen).
        if laeuft():
            stoppen(merker_weg=False)
        n = gewaehlt()
        if not n:
            messagebox.showinfo("Hinweis", "Erst einen Patch in der Liste wählen.")
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        w = phrase_wav(alle[n])
        if not w:
            messagebox.showerror("Fehler", "Patch%d hat keine WAV." % n)
            return
        # Nahtloses Weiterspielen: lieber eine einzige Datei aus mehreren
        # Patches bauen als jeden Patch einzeln zu starten (das ergab eine
        # hörbare Pause, Rückmeldung vom 10.11.2026).
        zustand["abschnitte"] = []
        datei, abschnitte = (None, [])
        if weiter_var.get() or zustand.get("vorgemerkt"):
            datei, abschnitte = kette_bauen(n, 0.0, zustand.get("vorgemerkt"))
        if datei:
            zustand["spieler"] = abspielen_datei(datei)
            zustand["temp"] = datei
            zustand["abschnitte"] = abschnitte
        else:
            zustand["spieler"] = abspielen_datei(w)
        zustand["vorgemerkt"] = None
        zustand["t0"] = time.time(); zustand["start"] = 0.0
        zustand["spielend"] = n
        zustand["marke"] = 0.0
        zeichne_welle()
        if knopf_stopp is not None:
            knopf_stopp.config(state="normal")
        status_var.set("spielt: %s" % patch_name(alle[n]))
        plane_umschalten()
        plane_beobachten(300)

    def pegel_lesen():
        """Gibt (gain, normieren) zurück; wirft ValueError bei Unsinn."""
        if normieren_var.get():
            return None, True
        roh = pegel_eingabe.get().strip().replace(",", ".").replace("+", "")
        if not roh:
            return None, False
        return float(roh), False

    def aufspielen():
        if not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst eine Karte wählen.")
            return
        datei = filedialog.askopenfilename(
            title="Audiodatei auswählen",
            filetypes=[("Audio", "*.wav *.aif *.aiff *.aifc *.caf *.mp3 *.m4a *.aac "
                                 "*.flac *.ogg *.opus"),
                        ("Alle", "*.*")])
        if not datei:
            return

        # Zu lange Dateien: gleich anbieten zu kürzen statt nur abzulehnen
        kuerzen = False
        dauer_datei = wav_info(datei)[0]
        if dauer_datei > MAX_LOOP_SEKUNDEN:
            if not messagebox.askokcancel("Datei zu lang",
                    "Die Datei ist %.1f Minuten lang.\n\n"
                    "Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n"
                    "(Handbuch, Seite 60). Längere Loops erscheinen auf der Karte,\n"
                    "werden vom Pedal aber nicht erkannt und können verschwinden.\n\n"
                    "Auf 9:59 kürzen? Der Schnitt liegt am Ende; die letzten\n"
                    "12 ms werden ausgeblendet, damit es nicht knackt."
                    % (dauer_datei / 60.0, MAX_LOOP_SEKUNDEN // 60)):
                return
            kuerzen = True

        if not messagebox.askokcancel("Aufspielen",
                "Die Karte wird verändert:\n\n%s\n\nFortfahren?"
                % os.path.basename(datei)):
            return
        try:
            gain_wert, normieren = pegel_lesen()
        except ValueError:
            messagebox.showerror("Fehler",
                "Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3), "
                "oder das Feld leer lassen.")
            return
        try:
            st = None if wahl_wert(stopp_wahl) == "wie Vorlage" else wahl_wert(stopp_wahl)
            rh = None if wahl_wert(rhythmus_wahl) == "wie Vorlage" else wahl_wert(rhythmus_wahl)
            wg = None if wahl_wert(wiedergabe_wahl) == "wie Vorlage" else wahl_wert(wiedergabe_wahl)
            fade_wert = None
            roh = fade_eingabe.get().strip().replace(",", ".")
            if roh:
                try:
                    fade_wert = float(roh)
                except ValueError:
                    messagebox.showerror("Fehler", "Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.")
                    return
                if fade_wert <= 0:
                    fade_wert = None
            befehl_aufspielen(zustand["basis"], datei, None, True, st, rh, wg,
                              fade_wert, gain_wert, normieren,
                              begrenzen_var.get(), kuerzen=kuerzen)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def sichern():
        if not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst eine Karte wählen.")
            return
        ziel = filedialog.askdirectory(title="Zielordner für die Sicherung")
        if not ziel:
            return
        befehl_sichern(zustand["basis"], ziel, True)
        messagebox.showinfo("Sicherung", "Fertig. Ordner:\n%s" % ziel)

    def umbenennen():
        """Namen des gewählten Patches ändern."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo("Hinweis", "Erst einen Patch in der Liste wählen.")
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        alt = patch_name(alle[n])
        neu = simpledialog.askstring("Umbenennen",
                "Neuer Name für Patch%d:" % n, initialvalue=alt, parent=fenster)
        if neu is None:
            return
        neu = neu.strip()
        if not neu:
            messagebox.showinfo("Hinweis", "Der Name darf nicht leer sein.")
            return
        if neu == alt:
            return
        try:
            befehl_aendern(zustand["basis"], n, True, None, None, None, neu)
            neu_laden()
            status_var.set("umbenannt: %s → %s" % (alt, neu))
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def zuruecknehmen():
        """Sicherung des gewählten Patches zurückholen (z. B. Fade rückgängig)."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo("Hinweis", "Erst einen Patch in der Liste wählen.")
            return
        ordner = os.path.expanduser(SICHERUNG_WURZEL)
        kandidaten = zuruecknehmen_kandidaten(ordner, n)
        if not kandidaten:
            messagebox.showerror("Fehler",
                "Für Patch%d liegt in %s keine Sicherung mit Aufnahme."
                % (n, ordner))
            return
        quelle = kandidaten[0]
        name = os.path.basename(os.path.dirname(quelle))
        stand = name.replace("Sicherung_", "").replace("_", " ")
        if not messagebox.askokcancel("Sicherung zurückholen",
                "Patch%d wird aus dieser Sicherung wiederhergestellt:\n\n"
                "  %s\n\n"
                "Damit lässt sich zum Beispiel ein Fade zurücknehmen.\n"
                "Der jetzige Stand wird vorher gesichert." % (n, stand)):
            return
        try:
            befehl_zuruecknehmen(zustand["basis"], n, True)
            neu_laden()
            status_var.set("Patch%d zurückgeholt" % n)
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def pegel_zurueck():
        """Pegeländerung aus einem Protokoll zurückrechnen."""
        if not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst eine Karte wählen.")
            return
        ordner = os.path.expanduser(SICHERUNG_WURZEL)
        datei = filedialog.askopenfilename(
            title="Pegelprotokoll wählen",
            initialdir=ordner if os.path.isdir(ordner) else os.path.expanduser("~"),
            filetypes=[("Pegelprotokoll", "Pegelprotokoll_*.txt"), ("Alle", "*.*")])
        if not datei:
            return
        if not messagebox.askokcancel("Pegel zurückrechnen",
                "Die Pegeländerungen aus diesem Protokoll werden umgekehrt:\n\n"
                "%s\n\nVorher wird alles gesichert. Fortfahren?"
                % os.path.basename(datei)):
            return
        try:
            befehl_pegel_umkehren(zustand["basis"], datei, True)
            neu_laden()
            status_var.set("Pegel zurückgerechnet")
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def datei_teilen():
        """Eine Datei in mehrere Patches aufteilen."""
        if not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst eine Karte wählen.")
            return
        try:
            anzahl = int(teile_eingabe.get().strip() or "2")
        except ValueError:
            messagebox.showerror("Fehler", "Bei Teile bitte eine ganze Zahl eintragen.")
            return
        if not (2 <= anzahl <= 60):
            messagebox.showerror("Fehler", "Bitte 2 bis 60 Teile angeben.")
            return
        datei = filedialog.askopenfilename(
            title="Audiodatei zum Teilen auswählen",
            filetypes=[("Audio", "*.wav *.aif *.aiff *.aifc *.caf *.mp3 *.m4a *.aac "
                                 "*.flac *.ogg *.opus"),
                        ("Alle", "*.*")])
        if not datei:
            return
        dauer = wav_info(datei)[0]
        if dauer <= 0:
            messagebox.showerror("Fehler", "Die Datei ist nicht lesbar.")
            return
        art = ("an Pausen geschnitten" if sinnvoll_var.get()
               else "gleichmäßig geschnitten")
        if not messagebox.askokcancel("Datei teilen",
                "%s\n\n%s wird in %d Teile geteilt (%s).\n"
                "Jeder Teil landet auf einem freien Platz.\n\nFortfahren?"
                % (os.path.basename(datei), sekunden(dauer), anzahl, art)):
            return
        def melde(text):
            status_var.set(text)
            fenster.update_idletasks(); fenster.update()
        try:
            befehl_teilen(zustand["basis"], datei,
                          os.path.splitext(os.path.basename(datei))[0],
                          True, anzahl, None, None, None, None,
                          sinnvoll=sinnvoll_var.get())
            neu_laden()
            status_var.set("in %d Patches geteilt" % anzahl)
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def alle_normieren():
        """Alle Patches auf gleiche Lautheit bringen."""
        if not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst eine Karte wählen.")
            return
        if not messagebox.askokcancel("Alle Patches normieren",
                "Alle Patches werden auf gleiche Lautheit gebracht:\n\n"
                "  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\n"
                "Vorher wird die ganze Karte gesichert - das dauert bei\n"
                "900 MB ein bis zwei Minuten. Fortfahren?"):
            return
        def melde(text):
            status_var.set(text)
            fenster.update_idletasks()
            fenster.update()
        try:
            befehl_alle_normieren(zustand["basis"], True, fortschritt=melde)
            neu_laden()
            status_var.set("alle Patches normiert")
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def platz_aendern():
        """Patch auf eine bestimmte Platznummer legen."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo("Hinweis", "Erst einen Patch in der Liste wählen.")
            return
        neu = simpledialog.askinteger("Platz ändern",
                "Patch%d soll auf welchen Platz?\n"
                "(der Platz muss frei sein)" % n, parent=fenster, minvalue=1, maxvalue=999)
        if not neu or neu == n:
            return
        try:
            befehl_platzieren(zustand["basis"], n, neu, True)
            neu_laden()
            status_var.set("Patch%d ist jetzt auf Platz %d" % (n, neu))
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def tauschen():
        """Zwei markierte Patches tauschen die Plätze."""
        i = liste_auswahl()
        if len(i) != 2:
            messagebox.showinfo("Hinweis",
                "Zum Tauschen genau ZWEI Patches markieren:\n"
                "ersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘).")
            return
        alle = [nr for nr, _, _ in patches(zustand["basis"])]
        a, b = alle[i[0]], alle[i[1]]
        if not messagebox.askokcancel("Tauschen",
                "Patch%d und Patch%d tauschen die Plätze?\n\n"
                "Der Inhalt bleibt, nur die Ordnernamen wechseln. "
                "Vorher wird die Reihenfolge gesichert." % (a, b)):
            return
        try:
            befehl_tauschen(zustand["basis"], a, b, True)
            neu_laden()
            liste_waehlen(i[0], mehrfach=True)
            liste_waehlen(i[1], mehrfach=True)
            status_var.set("getauscht: Patch%d ↔ Patch%d" % (a, b))
        except SystemExit as f:
            messagebox.showerror("Fehler", str(f))

    def einstellungen_anwenden():
        """Setzt die drei Auswahlfelder am GEWÄHLTEN Patch."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo("Hinweis", "Erst einen Patch in der Liste wählen.")
            return
        st = None if wahl_wert(stopp_wahl) == "wie Vorlage" else wahl_wert(stopp_wahl)
        rh = None if wahl_wert(rhythmus_wahl) == "wie Vorlage" else wahl_wert(rhythmus_wahl)
        wg = None if wahl_wert(wiedergabe_wahl) == "wie Vorlage" else wahl_wert(wiedergabe_wahl)
        fade_wert = None
        roh = fade_eingabe.get().strip().replace(",", ".")
        if roh:
            try:
                fade_wert = float(roh)
            except ValueError:
                messagebox.showerror("Fehler",
                    "Bei Fade am Ende bitte eine Zahl in Sekunden eintragen.")
                return
            if fade_wert <= 0:
                fade_wert = None
        try:
            gain_wert, normieren = pegel_lesen()
        except ValueError:
            messagebox.showerror("Fehler",
                "Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3).")
            return
        if not (st or rh or wg or fade_wert or gain_wert or normieren):
            messagebox.showinfo("Hinweis",
                "Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern.")
            return
        teile = []
        if st: teile.append("Stopp: %s" % st)
        if wg: teile.append("Wiedergabe: %s" % wg)
        if rh: teile.append("Rhythmus: %s" % rh)
        if fade_wert:
            teile.append("Fade: letzte %.1f s ausblenden" % fade_wert)
        if normieren:
            teile.append("Pegel: auf −1 dB Spitze normieren")
        elif gain_wert:
            teile.append("Pegel: %+.1f dB" % gain_wert)
        if fade_wert or gain_wert or normieren:
            teile.append("")
            teile.append("Achtung: Das verändert die Aufnahme selbst. "
                         "Zweimal angewendet wirkt es zweimal.")
        if not messagebox.askokcancel("Einstellungen anwenden",
                "Patch%d bekommt:\n\n%s\n\nVorher wird gesichert. Fortfahren?"
                % (n, "\n".join(teile))):
            return
        try:
            befehl_aendern(zustand["basis"], n, True, st, rh, wg, None, None,
                           fade_wert, gain_wert, normieren, begrenzen_var.get())
            neu_laden()
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def verschieben(schritt):
        n = gewaehlt()
        if not n or not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst einen Patch wählen.")
            return
        alle = [nr for nr, _, _ in patches(zustand["basis"])]
        platz = alle.index(n) + 1 + schritt if n in alle else 0
        if not (1 <= platz <= len(alle)):
            return
        try:
            befehl_verschieben(zustand["basis"], alle.index(n) + 1, platz, True)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def loeschen():
        n = gewaehlt()
        if not n or not zustand["basis"]:
            messagebox.showinfo("Hinweis", "Erst einen Patch wählen.")
            return
        if not messagebox.askokcancel("Löschen",
                "Patch%d wird von der Karte entfernt.\n\n"
                "Vorher wird er auf dem Mac gesichert. Fortfahren?" % n):
            return
        try:
            befehl_loeschen(zustand["basis"], n, True)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    def karte_auswerfen():
        if laeuft():
            stoppen()
        if not messagebox.askokcancel("Auswerfen",
                "Karte auswerfen? Danach kann sie abgezogen werden."):
            return
        quelle = zustand["basis"] or pfad_var.get()
        try:
            befehl_auswerfen(quelle if os.path.isdir(quelle) else standard_karte())
            zustand["basis"] = None
            pfad_var.set("keine Karte gefunden")
            liste_setzen([{"text": "  Karte ausgeworfen. Neue Karte einlegen und Karte wählen."}])
            zustand["welle"] = []
            zustand["dauer"] = 0.0
            zustand["marke"] = None
            zeichne_welle()
            zeit_var.set(T("kein Patch gewählt"))
            fenster.title("JamMan Solo XT")
        except SystemExit as e:
            messagebox.showerror("Fehler", str(e))

    # --- Auswahlfelder -----------------------------------------------------
    wahl = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    wahl.pack(pady=(4, 2))
    # Gilt für beide Wege: "Datei aufspielen" und "Einstellungen anwenden".
    # "Beim Aufspielen" war irreführend, weil dieselben Felder auch einen
    # vorhandenen Patch ändern.
    tk.Label(wahl, text=T("Einstellungen:"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(0, 10))
    for beschriftung, werte in (("Stoppart", ("wie Vorlage", "fade", "instant", "finish")),
                                ("Rhythmus", ("wie Vorlage", "silence", "kick")),
                                ("Wiedergabe", ("wie Vorlage", "loop", "single"))):
        tk.Label(wahl, text=T(beschriftung), bg=FARBE_HINTERGRUND,
                 fg=FARBE_LEISE).pack(side="left")
        # Anzeige übersetzt, gespeichert wird der deutsche Wert
        anzeige = [T(w) for w in werte]
        var = tk.StringVar(value=T("wie Vorlage"))
        tk.OptionMenu(wahl, var, *anzeige).pack(side="left", padx=(2, 14))
        if beschriftung == "Stoppart":
            stopp_wahl = var
        elif beschriftung == "Rhythmus":
            rhythmus_wahl = var
        else:
            wiedergabe_wahl = var

    # --- Knöpfe: zwei Reihen, damit nichts abgeschnitten wird ---------------
    def knopfreihe(zeile):
        rahmen2 = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
        rahmen2.pack(fill="x", padx=14, pady=2)
        return rahmen2

    knopf_ton = None
    knopf_stopp = None

    knopf_refs = {}

    def baue_reihe(nummer, eintraege):
        rahmen3 = knopfreihe(nummer)
        for spalte, (beschriftung, befehl) in enumerate(eintraege):
            knopf = tk.Button(rahmen3, text=T(beschriftung), command=befehl)
            knopf_refs[beschriftung] = knopf
            knopf.grid(row=0, column=spalte, sticky="ew", padx=3)
            rahmen3.grid_columnconfigure(spalte, weight=1, uniform="r%d" % nummer)
        return rahmen3

    baue_reihe(1, (("Karte wählen", karte_waehlen),
                   ("Datei aufspielen", aufspielen),
                   ("Alles sichern", sichern),
                   ("Pegel zurückrechnen", pegel_zurueck),
                   ("Neu laden", neu_laden)))

    reihe2 = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    reihe2.pack(fill="x", padx=14, pady=2)
    for spalte, (beschriftung, befehl) in enumerate(
            (("Anhören", abspielen),
             ("Stopp", stoppen),
             ("Umbenennen", umbenennen),
             ("Platz ändern", platz_aendern),
             ("Sicherung zurückholen", zuruecknehmen),
             ("Einstellungen anwenden", einstellungen_anwenden))):
        knopf = tk.Button(reihe2, text=T(beschriftung), command=befehl)
        knopf_refs[beschriftung] = knopf
        knopf.grid(row=0, column=spalte, sticky="ew", padx=3)
        reihe2.grid_columnconfigure(spalte, weight=1, uniform="r2")
        if beschriftung == "Anhören":
            knopf_ton = knopf
        elif beschriftung == "Stopp":
            knopf_stopp = knopf

    baue_reihe(3, (("▲ nach oben", lambda: verschieben(-1)),
                   ("▼ nach unten", lambda: verschieben(1)),
                   ("Tauschen", tauschen),
                   ("Datei teilen", datei_teilen),
                   ("Alle normieren", alle_normieren),
                   ("Löschen", loeschen),
                   ("Karte auswerfen", karte_auswerfen)))

    braucht_auswahl = ("Anhören", "Umbenennen", "Platz ändern", "Tauschen",
                       "Löschen", "Datei teilen", "▲ nach oben", "▼ nach unten",
                       "Einstellungen anwenden")

    def knopf_zustand():
        hat = bool(liste_auswahl())
        for name in braucht_auswahl:
            k = knopf_refs.get(name)
            if k is not None:
                k.config(state="normal" if hat else "disabled")

    tk.Label(wahl, text=T("Fade am Ende"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(10, 2))
    fade_eingabe = tk.Entry(wahl, width=5, justify="center",
                            bg="#ffffff", fg="#000000", insertbackground="#000000",
                            relief="solid", borderwidth=1)
    fade_eingabe.pack(side="left")
    tk.Label(wahl, text="s", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")

    wahl2 = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    wahl2.pack(pady=(2, 0))
    tk.Label(wahl2, text=T("Pegel"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(0, 4))
    pegel_eingabe = tk.Entry(wahl2, width=6, justify="center",
                             bg="#ffffff", fg="#000000", insertbackground="#000000",
                             relief="solid", borderwidth=1)
    pegel_eingabe.pack(side="left")
    tk.Label(wahl2, text="dB", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(3, 14))
    normieren_var = tk.BooleanVar(value=False)
    tk.Checkbutton(wahl2, text=T("auf −1 dB Spitze normieren"),
                   variable=normieren_var, bg=FARBE_HINTERGRUND,
                   fg=FARBE_LEISE, activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left", padx=(0, 16))
    begrenzen_var = tk.BooleanVar(value=True)
    tk.Label(wahl2, text=T("Teile"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(16, 4))
    teile_eingabe = tk.Entry(wahl2, width=4, justify="center",
                             bg="#ffffff", fg="#000000", insertbackground="#000000",
                             relief="solid", borderwidth=1)
    teile_eingabe.insert(0, "2")
    teile_eingabe.pack(side="left")
    sinnvoll_var = tk.BooleanVar(value=True)
    tk.Checkbutton(wahl2, text=T("an Pausen schneiden"), variable=sinnvoll_var,
                   bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                   activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left", padx=(8, 0))
    tk.Checkbutton(wahl2, text=T("Anhebung auf 0 dB begrenzen"),
                   variable=begrenzen_var, bg=FARBE_HINTERGRUND,
                   fg=FARBE_LEISE, activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left")

    if knopf_stopp is not None:
        knopf_stopp.config(state="disabled")

    tk.Label(fenster, textvariable=status_var, bg=FARBE_HINTERGRUND,
             fg=FARBE_TON).pack(pady=(6, 0))
    tk.Label(fenster, text=T("Doppelklick spielt · Ziehen ordnet um · ⌘ für zwei Markierungen, dann Tauschen · Esc hält an."),
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE).pack(pady=(0, 10))

    # --- Ziehen und Ablegen: Reihenfolge ändern -------------------------
    def zieh_start(e):
        strich_weg()
        i = liste_index(e.y)
        zustand["von"] = i if 0 <= i < len(zustand["zeilen"]) else None
        # Auswahl: einfacher Klick wählt, ⌘ klickt dazu
        if zustand["von"] is not None:
            mehrfach = bool(e.state & 0x0008)      # Command-Taste
            liste_waehlen(zustand["von"], mehrfach=mehrfach)
            welle_laden()
        zustand["strich"] = None
        liste.config(cursor="fleur")
        if zustand["zeilen"]:
            status_var.set("ziehen: %s" % zustand["zeilen"][zustand["von"]]["text"].split()[1]
                           if zustand["von"] is not None else "")
        auto_scroll()

    def auto_scroll():
        """Beim Ziehen an den Rand scrollen - sonst ist es Blindflug.

        Läuft, solange gezogen wird, und plant sich selbst neu ein.
        """
        if zustand.get("von") is None:
            zustand["auto"] = None
            return
        y = zustand.get("maus_y")
        if y is not None:
            hoehe = liste.winfo_height() or 400
            schritt = 0
            if y < ZEILE_H * 1.5:
                schritt = -1
            elif y > hoehe - ZEILE_H * 1.5:
                schritt = 1
            if schritt:
                liste.yview_scroll(schritt, "units")
        zustand["auto"] = fenster.after(90, auto_scroll)
        zustand["ziel"] = None
        liste.config(cursor="fleur")          # Mauszeiger zeigt: es wird bewegt
        if zustand["nummern"] and 0 <= zustand["von"] < len(zustand["nummern"]):
            alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
            nr = zustand["nummern"][zustand["von"]]
            status_var.set("ziehen: %s" % patch_name(alle[nr]))

    def zieh_bewegen(e):
        zustand["maus_y"] = e.y
        # Beim Ziehen neu bestimmen, wo der Strich stehen soll
        strich_weg()
        pos = liste.nearest(e.y)
        if pos < 0:
            pos = liste.size()
        strich_setzen(pos)
        strich = zustand.get("marker")
        ziel = strich
        von = zustand.get("von")
        if von is not None and strich > von:
            ziel = strich          # Einfügen hinter der gezogenen Zeile
        if ziel >= len(zustand["zeilen"]):
            status_var.set("ablegen auf Platz %d (ans Ende)" % (ziel + 1))
        else:
            status_var.set("ablegen auf Platz %d" % (ziel + 1))

    def zieh_ende(e):
        von = zustand.get("von")
        strich = zustand.get("marker")
        zustand["von"] = None
        zustand["maus_y"] = None
        if zustand.get("auto"):
            try:
                fenster.after_cancel(zustand["auto"])
            except Exception:
                pass
            zustand["auto"] = None
        liste.config(cursor="arrow")
        strich_weg()
        if von is None or strich is None or not zustand["basis"]:
            status_var.set("")
            return
        nach = strich if strich < von else strich - 1
        alle = [nr for nr, _, _ in patches(zustand["basis"])]
        if von == nach or not (0 <= von < len(alle) and 0 <= nach < len(alle)):
            status_var.set("")
            return
        try:
            befehl_verschieben(zustand["basis"], von + 1, nach + 1, True, kopie=False)
            neu_laden()
            liste_waehlen(nach)
            liste_zeigen(nach)
            status_var.set("verschoben: %s → Platz %d" % (alle[von], nach + 1))
        except SystemExit as f:
            messagebox.showerror("Fehler", str(f))

    leinwand.bind("<Button-1>", springe)
    def umplan_welle(e=None):
        if zustand.get("t_welle"):
            try:
                fenster.after_cancel(zustand["t_welle"])
            except Exception:
                pass
        def spaeter():
            zustand["t_welle"] = None
            zeichne_welle()
        zustand["t_welle"] = fenster.after(150, spaeter)

    leinwand.bind("<Configure>", umplan_welle)
    liste.bind("<Enter>", lambda e: liste.focus_set())

    # Klicks irgendwo im Fenster geben den Tastatur-/Rad-Fokus an die Liste
    # zurück. Auf macOS bekommt das Rad Ereignisse des Widgets mit Fokus.
    def fokus_zurueck(e=None):
        """Fokus zurück an die Liste - aber NICHT aus Eingabefeldern.

        Sonst kann man in "Platz", "Pegel", "Fade" oder "Teile" nichts
        eintippen (am 10.11.2026 gemeldet).
        """
        try:
            widget = e.widget if e is not None else None
            if widget is None or widget is liste:
                return
            if widget.winfo_class() in ("Entry", "Spinbox", "TEntry"):
                return
            fenster.after(20, liste.focus_set)
        except Exception:
            pass

    fenster.bind("<ButtonRelease-1>", fokus_zurueck, add="+")
    fenster.bind("<FocusIn>", lambda e: liste.focus_set(), add="+")
    liste.focus_set()
    def umplan_liste(e=None):
        if zustand.get("t_liste"):
            try:
                fenster.after_cancel(zustand["t_liste"])
            except Exception:
                pass
        def spaeter():
            zustand["t_liste"] = None
            liste_zeichnen()
        zustand["t_liste"] = fenster.after(120, spaeter)

    liste.bind("<Configure>", umplan_liste)
    liste.bind("<Key>", tippen)
    fenster.bind("<Key>", tippen, add="+")
    fenster.bind("<F2>", lambda e: vormerken())
    fenster.bind("<F3>", lambda e: gehe_zu())
    def auswahl_geaendert(e=None):
        knopf_zustand()
        welle_laden()

    liste.bind("<<ListboxSelect>>", auswahl_geaendert)
    liste.bind("<ButtonPress-1>", zieh_start, add="+")
    liste.bind("<B1-Motion>", zieh_bewegen, add="+")
    liste.bind("<ButtonRelease-1>", zieh_ende, add="+")
    def beim_schliessen():
        """Ton beenden, dann das Fenster schließen.

        Am 10.11.2026 lief nach dem Schließen die Wiedergabe weiter -
        der Tonprozess war ein Kindprozess und blieb übrig.
        """
        if laeuft():
            stoppen()
        fenster.destroy()

    fenster.protocol("WM_DELETE_WINDOW", beim_schliessen)

    liste.bind("<Double-Button-1>", lambda e: abspielen())
    fenster.bind("<Escape>", lambda e: stoppen())

    def feld_fokus(e):
        """Klick in ein Eingabefeld gibt ihm den Tastaturfokus."""
        e.widget.focus_set()

    def alle_felder(w):
        for kind in w.winfo_children():
            if kind.winfo_class() == "Entry":
                kind.bind("<Button-1>", feld_fokus, add="+")
            alle_felder(kind)

    alle_felder(fenster)
    knopf_zustand()

    neu_laden()
    # Erst nach dem ersten Layout steht die Höhe der Liste fest - sonst
    # wird beim Start nur eine Zeile gezeichnet.
    fenster.update_idletasks()
    liste_zeichnen()
    zeichne_welle()
    # Fenstergröße erst jetzt bestimmen - vorher gibt es noch keine
    # Bedienelemente und die Messung wäre wertlos.
    fenster.update_idletasks()
    fenster.title("JamMan Solo XT")
    # Größe aus dem Bedarf der Bedienelemente - sonst werden die rechten
    # Knöpfe abgeschnitten (am 10.11.2026 gesehen: 1162 statt 1080 px).
    fenster.update_idletasks()
    bildschirm_b = fenster.winfo_screenwidth()
    bildschirm_h = fenster.winfo_screenheight()
    breite = min(max(1000, fenster.winfo_reqwidth() + 24), bildschirm_b - 40)
    hoehe = min(max(720, fenster.winfo_reqheight() + 24), bildschirm_h - 80)
    fenster.geometry("%dx%d" % (breite, hoehe))
    fenster.minsize(min(breite, 1000), min(hoehe, 700))

    fenster.mainloop()


# -------------------------------------------------------------------- Start

def main():
    # Sprache VOR dem Bau des Parsers bestimmen - die Hilfetexte werden beim
    # Anlegen übersetzt, nicht beim Ausgeben.
    for i, arg in enumerate(sys.argv):
        if arg == "--sprache" and i + 1 < len(sys.argv):
            sprache_setzen(sys.argv[i + 1])
        elif arg.startswith("--sprache="):
            sprache_setzen(arg.split("=", 1)[1])
    p = argparse.ArgumentParser(
        description=T("JamMan Solo XT — Speicherkarte verwalten"),
        epilog=befehlsliste(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="JamMan-Werkzeug " + FASSUNG)
    p.add_argument("befehl", metavar="BEFEHL",
                   choices=sorted(list(BEFEHLE) + list(BEFEHL_ALIAS)),
                   help=T("was zu tun ist (Liste unten; englische Namen gehen auch)"))
    p.add_argument("--sprache", choices=sorted(SPRACHEN),
                   help=T("Sprache der Ausgabe (Vorgabe: aus dem System)"))
    p.add_argument("--karte", default=standard_karte(),
                   help=T("Laufwerk der Karte (Vorgabe: automatisch erkannt)"))
    p.add_argument("--ziel", default="~/JamMan-Sicherung", help=T("Zielordner für 'sichern'"))
    p.add_argument("--wav", help=T("Audiodatei für 'aufspielen'"))
    p.add_argument("--name", help=T("Name des Patches (Vorgabe: Dateiname)"))
    p.add_argument("--patch", type=int, help=T("Patch-Nummer"))
    p.add_argument("--nach", type=int, help=T("Zielplatz für 'verschieben'"))
    p.add_argument("--mit", type=int, help=T("Tauschpartner für 'tauschen'"))
    p.add_argument("--auf", type=int, help=T("Zielplatz für 'platzieren'"))
    p.add_argument("--aus", help=T("Sicherungsordner für 'zuruecknehmen'"))
    p.add_argument("--protokoll", help=T("Pegelprotokoll für 'pegel-umkehren'"))
    p.add_argument("--alle", action="store_true",
                   help=T("bei 'zuruecknehmen': alle Patches aus der Sicherung"))
    p.add_argument("--ziel-rms", type=float, default=-18.0,
                   help=T("Ziellautheit für 'alle-normieren' (Vorgabe -18 dB)"))
    p.add_argument("--grenze", type=float, default=-1.0,
                   help=T("Spitzengrenze für 'alle-normieren' (Vorgabe -1 dB)"))
    p.add_argument("--kuerzen", action="store_true",
                   help=T("zu lange Datei auf 9:59 kürzen"))
    p.add_argument("--mit-pause", action="store_true",
                   help=T("beim Teilen die Pause an der Naht stehen lassen"))
    p.add_argument("--einfach", action="store_true",
                   help=T("beim Teilen gleichmäßig schneiden statt an Pausen"))
    p.add_argument("--teile", type=int, metavar="N",
                   help=T("Datei in N gleich lange Patches aufteilen"))
    p.add_argument("--laenge", type=float, metavar="SEKUNDEN",
                   help=T("zu lange Datei auf diese Länge kürzen"))
    p.add_argument("--trotzdem", action="store_true",
                   help=T("auch Loops über 10 Minuten schreiben (Pedal nimmt sie nicht an)"))
    p.add_argument("--fade", type=float, metavar="SEKUNDEN",
                   help=T("Fade am Ende der Datei einrechnen (z. B. 3)"))
    p.add_argument("--gain", type=float, metavar="DB",
                   help=T("Lautstärke um DB ändern (z. B. -3 oder +2)"))
    p.add_argument("--ohne-begrenzung", action="store_true",
                   help=T("Anhebung NICHT auf 0 dBFS begrenzen (kann übersteuern)"))
    p.add_argument("--normieren", action="store_true",
                   help=T("auf -1 dB Spitzenpegel normieren"))
    p.add_argument("--stopp", choices=["fade", "instant", "finish"],
                   help=T("Stopp-Verhalten des neuen Patches (ohne Angabe: wie Vorlage)"))
    p.add_argument("--wiedergabe", choices=["loop", "single"],
                   help=T("Loop oder Single (IsLoop in phrase.xml; ohne Angabe: wie Vorlage)"))
    p.add_argument("--rhythmus", choices=["silence", "kick"],
                   help=T("Rhythmus des neuen Patches (ohne Angabe: wie Vorlage)"))
    p.add_argument("--keine-kopie", action="store_true",
                   help=T("beim Verschieben nur die Namen sichern (schnell, kein Kopieren)"))
    p.add_argument("--sicherung", default="~/JamMan-Sicherung",
                   help=T("Ordner für die Sicherung vor Löschen/Verschieben"))
    p.add_argument("--schreiben", action="store_true", help=T("wirklich verändern (sonst Trockenlauf)"))
    a = p.parse_args()
    sprache_setzen(a.sprache or einstellung_lesen("sprache") or sprache_aus_system())
    a.befehl = BEFEHL_ALIAS.get(a.befehl, a.befehl)

    if a.befehl == "gui":
        gui(a.karte)
        return
    if a.befehl == "selbsttest":
        selbsttest(a.karte)
        return
    if a.befehl == "menu":
        try:
            menu(a.karte)
        except KeyboardInterrupt:
            print()
        return
    if a.befehl == "auswerfen":
        befehl_auswerfen(a.karte)
        return

    basis = karte_pruefen(a.karte)
    if a.befehl == "liste":
        befehl_liste(basis)
    elif a.befehl == "namensform":
        befehl_namensform(basis, a.schreiben, a.sicherung)
    elif a.befehl == "sichern":
        befehl_sichern(basis, a.ziel, a.schreiben)
    elif a.befehl == "aufspielen":
        if not a.wav:
            raise SystemExit("Fehler: --wav fehlt.")
        befehl_aufspielen(basis, a.wav, a.name, a.schreiben, a.stopp, a.rhythmus,
                          a.wiedergabe, a.fade, a.gain, a.normieren,
                          not a.ohne_begrenzung, a.trotzdem,
                          a.kuerzen, a.laenge)
    elif a.befehl == "abspielen":
        if not a.patch:
            raise SystemExit("Fehler: --patch fehlt.")
        befehl_abspielen(basis, a.patch)
    elif a.befehl == "loeschen":
        if not a.patch:
            raise SystemExit("Fehler: --patch fehlt.")
        befehl_loeschen(basis, a.patch, a.schreiben, a.sicherung)
    elif a.befehl == "teilen":
        if not a.wav:
            raise SystemExit("Fehler: --wav fehlt.")
        befehl_teilen(basis, a.wav, a.name or os.path.splitext(os.path.basename(a.wav))[0],
                      a.schreiben, a.teile, a.laenge, a.sicherung,
                      a.stopp, a.wiedergabe, sinnvoll=not a.einfach,
                      ohne_pause=not a.mit_pause)
    elif a.befehl == "pegel-umkehren":
        if not a.protokoll:
            raise SystemExit("Fehler: --protokoll fehlt.")
        befehl_pegel_umkehren(basis, os.path.expanduser(a.protokoll),
                              a.schreiben, a.sicherung)
    elif a.befehl == "alle-normieren":
        befehl_alle_normieren(basis, a.schreiben, a.ziel_rms, a.grenze, a.sicherung)
    elif a.befehl == "zuruecknehmen":
        if a.alle:
            befehl_zuruecknehmen_alle(basis, a.aus, a.schreiben, a.sicherung)
        else:
            if not a.patch:
                raise SystemExit("Fehler: --patch fehlt (oder --alle angeben).")
            befehl_zuruecknehmen(basis, a.patch, a.schreiben, a.sicherung, a.aus)
    elif a.befehl == "platzieren":
        if not a.patch or not a.auf:
            raise SystemExit("Fehler: --patch und --auf fehlen.")
        befehl_platzieren(basis, a.patch, a.auf, a.schreiben, a.sicherung)
    elif a.befehl == "tauschen":
        if not a.patch or not a.mit:
            raise SystemExit("Fehler: --patch und --mit fehlen.")
        befehl_tauschen(basis, a.patch, a.mit, a.schreiben, a.sicherung,
                        kopie=not a.keine_kopie)
    elif a.befehl == "aendern":
        if not a.patch:
            raise SystemExit("Fehler: --patch fehlt.")
        befehl_aendern(basis, a.patch, a.schreiben, a.stopp, a.rhythmus,
                       a.wiedergabe, a.name, a.sicherung, a.fade,
                       a.gain, a.normieren, not a.ohne_begrenzung)
    elif a.befehl == "saeubern":
        befehl_saeubern(basis, a.schreiben)
    elif a.befehl == "verschieben":
        if not a.patch or not a.nach:
            raise SystemExit("Fehler: --patch und --nach fehlen.")
        befehl_verschieben(basis, a.patch, a.nach, a.schreiben, a.sicherung,
                           kopie=not a.keine_kopie)


if __name__ == "__main__":
    main()
