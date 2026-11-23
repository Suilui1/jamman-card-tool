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
import threading
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
WIEDERGABE = {"loop": "1", "single": "0",
              "loop \u21ba": "1", "single \u21ba": "0"}

# Handbuch JamMan Solo XT, Seite 60 (Spezifikationen):
#   "Max. Individual Loop Time: 10 minutes (stereo)"
#   "Loop File Format: 44.1 kHz, 16-bit (stereo), uncompressed WAV"
# Längere Loops nimmt das Pedal nicht an - am 10.11.2026 mit einer
# 15-Minuten-Datei erlebt: auf der Karte vorhanden, im JamMan nicht,
# danach war der Ordner auf dem Mac verschwunden.
MAX_LOOP_SEKUNDEN = 600

WOLL = (44100, 16, 2)          # was der JamMan erwartet: 44,1 kHz, 16 Bit, Stereo

FASSUNG = "3.136.18 (23.11.2026)"
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
            zeilen.append(T("  %-28s  (gibt es nicht)") % wurzel)
            continue
        try:
            namen = sorted(os.listdir(wurzel))
        except OSError as e:
            zeilen.append(T("  %-28s  (nicht lesbar: %s)") % (wurzel, e.strerror))
            continue
        if not namen:
            zeilen.append(T("  %-28s  (leer)") % wurzel)
            continue
        for name in namen:
            p = os.path.join(wurzel, name)
            if not os.path.isdir(p):
                continue
            unter = []
            try:
                unter = os.listdir(p)
            except OSError as e:
                zeilen.append(T("  %-28s  (nicht lesbar: %s)") % (p, e.strerror))
                continue
            treffer_name = kartenordner_in(p)
            if treffer_name:
                ordner = os.path.join(p, treffer_name)
                try:
                    anz = len([x for x in os.listdir(ordner)
                               if re.fullmatch(r"Patch\d+", x, re.IGNORECASE)])
                except OSError:
                    anz = -1
                zeilen.append(T("  %-28s  %s mit %s Patches")
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
    raise SystemExit(T("Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay)."))


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


def fade_ausblenden(pfad, sekunden):
    """Blendet die letzten Sekunden einer WAV-Datei aus (linear).

    Das Pedal blendet beim Stoppen fest über 10 Sekunden aus (Handbuch
    JamMan Solo XT, Seite 47); eine einstellbare Fade-Dauer kennt es
    nicht. Wer eine andere Länge will, muss sie in die Aufnahme legen -
    dann ist sie unabhängig vom Stopp-Verhalten.
    """
    import array
    import wave as _wave
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
    import array
    import math
    import wave as _wave
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
    import array
    import math
    import wave as _wave
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
        raise SystemExit(T("Fehler: '%s' gibt es nicht.") % pfad)
    except IsADirectoryError:
        raise SystemExit(T("Fehler: '%s' ist ein Ordner, keine Datei.") % pfad)
    except UnicodeDecodeError:
        raise SystemExit(T("Fehler: '%s' ist kein Pegelprotokoll "
                         "(keine lesbare Textdatei).") % pfad)
    except PermissionError:
        raise SystemExit(T("Fehler: '%s' darf nicht gelesen werden.") % pfad)
    except OSError as fehler:
        raise SystemExit(T("Fehler: '%s' lässt sich nicht lesen: %s")
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
        raise SystemExit(T("Fehler: im Protokoll stehen keine Einträge."))
    print(T("  Protokoll: %s") % protokoll)
    print(T("  %d Einträge") % len(eintraege))
    if not schreiben:
        for e in eintraege:
            print(T("    %-10s %-24s Faktor %.4f → 1/%.4f") % (
                e["ordner"], e["name"][:24], e["faktor"], e["faktor"]))
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return
    # Nur sichern, was auch geändert wird. Anlaß (19.11.2026): Hier wurde die
    # ganze Karte kopiert (bei 35 Patches 1,3 GB), obwohl das Protokoll oft
    # nur wenige Patches nennt.
    alle = dict((n, pf) for n, _, pf in patches(basis))
    betroffen = []
    for e in eintraege:
        for _nummer, pfad in alle.items():
            if os.path.basename(pfad) == e["ordner"]:
                if os.path.exists(os.path.join(pfad, "PhraseA", "phrase.wav")):
                    betroffen.append(pfad)
                break
    ziel = sicher_ablegen(basis, betroffen or [pf for _, _, pf in patches(basis)],
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
  4. Das JamMan Card Tool neu starten

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
                     os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
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


def einhaengepunkt(pfad):
    """Einhängepunkt des Datenträgers, in dem `pfad` liegt.

    Ohne fremde Programme: vom Ordner nach oben gehen, bis sich die
    Gerätekennung (st_dev) ändert. Leerer Rückgabewert heißt: der Pfad ist
    nicht (mehr) eingehängt — dann gibt es nichts auszuwerfen.
    Anlaß (17.11.2026): Bei Karte im Pedal stand hier „/media", und der
    Auswurfversuch scheiterte mit einer langen Fehlerkette.
    """
    try:
        pfad = os.path.abspath(os.path.expanduser(str(pfad)))
        if not os.path.exists(pfad):
            return ""
        kennung = os.stat(pfad).st_dev
    except OSError:
        return ""
    aktuell = pfad
    while True:
        eltern = os.path.dirname(aktuell)
        if eltern == aktuell:
            return ""
        try:
            if os.stat(eltern).st_dev != kennung:
                return aktuell
        except OSError:
            return aktuell
        aktuell = eltern


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
        # Linux, drei Wege von „braucht kein Recht" nach „braucht root".
        # Am 17.11.2026 fragte udisksctl nach einem Paßwort und scheiterte
        # danach — deshalb zuerst gio (läuft in der Benutzersitzung) und
        # udisksctl nur ohne Rückfrage.
        punkt = einhaengepunkt(karte)
        if not punkt:
            return False, T("Die Karte hängt nicht im Rechner — nichts "
                            "auszuwerfen. Steckt sie im Pedal, führt das "
                            "Pedal sie nicht als Laufwerk.")

        def lauf(befehl):
            """Befehl ausführen; ein fehlendes Programm ist kein Absturz."""
            try:
                return subprocess.run(befehl, capture_output=True, text=True)
            except FileNotFoundError:
                class _Fehlt:
                    returncode = 127
                    stdout = ""
                    stderr = "%s ist nicht vorhanden" % befehl[0]
                return _Fehlt()

        fehler = []
        if shutil.which("gio"):
            r = lauf(["gio", "mount", "-u", punkt])
            if r.returncode == 0:
                return True, "Karte abgemeldet."
            fehler.append("gio: " + (r.stderr.strip().splitlines() or ["unbekannt"])[0])

        r = lauf(["findmnt", "-n", "-o", "SOURCE", "--target", punkt])
        geraet = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        if geraet and shutil.which("udisksctl"):
            r = lauf(["udisksctl", "unmount", "-b", geraet,
                      "--no-user-interaction"])
            if r.returncode == 0:
                lauf(["udisksctl", "power-off", "-b", geraet,
                      "--no-user-interaction"])
                return True, "Karte abgemeldet."
            fehler.append("udisksctl: " + (r.stderr.strip().splitlines() or ["unbekannt"])[0])

        r = lauf(["umount", punkt])
        if r.returncode == 0:
            return True, "Karte abgemeldet."
        fehler.append("umount: " + (r.stderr.strip().splitlines() or ["unbekannt"])[0])

        # Nur den ersten Grund nennen — die ganze Kette hilft niemandem
        return False, (T("Auswerfen ging nicht. Bitte im Dateimanager auf "
                         "„Auswerfen\" klicken.") + "\n\n" + fehler[0])

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
KARTENORDNER = ("JamManSoloXT", "JamManStereo", "JamManDelay")
# Unbekanntes Gerät: nur lesen (20.11.2026). Der Ordner wird erkannt,
# aber geschrieben wird nichts — der Aufbau ist nicht belegt.
NUR_LESEN = False
# Delay: Von Plywood1107 vorgeschlagen (Pull Request #3, 20.11.2026).
# Der Kartenaufbau wird angenommen, nicht bestätigt — erkannt wird der
# Ordner, geschrieben wird erst nach Sicherung und auf Befehl.


def knopf_text(text, grenze=22):
    """Lange Knopftexte auf zwei Zeilen umbrechen.

    Anlaß (19.11.2026): „Pegeländerung zurücknehmen …" war deutsch und
    französisch abgeschnitten. Statt die Schrift zu verkleinern, bricht der
    Text an einer sinnvollen Stelle um — die Knopfreihe wird dadurch nicht
    breiter als der Bildschirm.
    """
    if len(text) <= grenze or " " not in text:
        return text
    woerter = text.split(" ")
    hälfte = len(text) // 2
    bester, abstand = None, 10 ** 6
    lauf = 0
    for i, wort in enumerate(woerter[:-1]):
        lauf += len(wort) + 1
        if abs(lauf - hälfte) < abstand:
            bester, abstand = i, abs(lauf - hälfte)
    if bester is None:
        return text
    return " ".join(woerter[:bester + 1]) + "\n" + " ".join(woerter[bester + 1:])


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


def _dateiname_sicher(name, ersatz="-"):
    """Einen Patch-Namen in einen Dateinamen verwandeln, der überall geht."""
    verboten = '\\:*?"<>|'
    sauber = ""
    for zeichen in str(name):
        if zeichen in "/\\:":
            sauber += " - "
        elif zeichen in verboten:
            sauber += ersatz
        else:
            sauber += zeichen
    sauber = " ".join(sauber.split())
    return sauber[:60] or "ohne-Namen"


def herausgeben(basis, ziel=None, melden=None):
    """Eine Karte so herausgeben, daß sie ohne dieses Werkzeug lesbar bleibt.

    Anlaß (20.11.2026): Retroactive sagt seinen Nutzern, wann Schluß ist und wie
    sie umsteigen. Für eine JamMan-Karte heißt das: die Tonspuren mit ihren
    Namen als gewöhnliche WAV-Dateien, dazu eine Liste mit allem, was in der
    Karte steht. Dann ist der Inhalt auch dann zu gebrauchen, wenn dieses
    Werkzeug einmal nicht mehr läuft oder ein Pedal stirbt.

    Geschrieben wird NICHTS auf der Karte — nur gelesen und kopiert.
    Rückgabe: (ordner, anzahl_wav, anzahl_ohne_ton)
    """
    import csv
    import shutil

    if not ziel:
        ziel = os.path.join(os.path.expanduser("~"), "JamMan-Herausgabe",
                            datetime.now().strftime("%Y-%m-%d_%H-%M"))
    os.makedirs(ziel, exist_ok=True)
    zeilen = []
    anzahl = 0
    ohne_ton = 0
    vergeben = set()
    for nummer, name, pfad in patches(basis):
        wav = phrase_wav(pfad)
        stop, rhy, loop, rueck, bpm = patch_einstellungen(pfad)
        dauer = wav_info(wav)[0] if wav else 0
        datei = ""
        if wav and os.path.exists(wav):
            stamm = "%02d - %s" % (nummer, _dateiname_sicher(patch_name(pfad)))
            datei = stamm + os.path.splitext(wav)[1].lower()
            zaehler = 2
            while datei.lower() in vergeben:
                datei = "%s (%d)%s" % (stamm, zaehler, os.path.splitext(wav)[1].lower())
                zaehler += 1
            vergeben.add(datei.lower())
            shutil.copy2(wav, os.path.join(ziel, datei))
            anzahl += 1
        else:
            ohne_ton += 1
        zeilen.append([nummer, patch_name(pfad), datei,
                       sekunden(dauer) if wav else "",
                       T(stop), T(loop) + (" ↺" if rueck else ""),
                       ("%d" % round(bpm)) if bpm else "", T(rhy)])
        if melden:
            try:
                melden(nummer, name)
            except Exception:
                pass

    kopf = [T("Platz"), T("Name"), T("Datei"), T("Dauer"), T("Stoppart"),
            T("Wiedergabe"), T("Tempo (BPM)"), T("Rhythmus")]
    with open(os.path.join(ziel, "karte.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        schreiber = csv.writer(f, delimiter=";")
        schreiber.writerow(kopf)
        schreiber.writerows(zeilen)

    with open(os.path.join(ziel, "LIESMICH.txt"), "w", encoding="utf-8") as f:
        f.write(T("Herausgabe einer JamMan-Karte") + "\n")
        f.write("=" * 40 + "\n\n")
        f.write(T("Gerät: %s") % (UNTERORDNER or "?") + "\n")
        f.write(T("Aufgenommen: %s") % datetime.now().strftime("%d.%m.%Y %H:%M") + "\n")
        f.write(T("Werkzeug: %s") % FASSUNG + "\n\n")
        f.write(T("In diesem Ordner liegen %d Tonspuren als gewöhnliche "
                  "WAV-Dateien, benannt nach ihrem Platz und ihrem Namen in der "
                  "Karte. %d Plätze hatten keinen Ton.") % (anzahl, ohne_ton) + "\n\n")
        f.write(T("karte.csv enthält zu jeder Spur: Platz, Name, Datei, Dauer, "
                  "Stoppart, Wiedergabe, Tempo und Rhythmus. Semikolon als "
                  "Trennzeichen, damit Excel und LibreOffice sie direkt öffnen.") + "\n\n")
        f.write(T("Diese Dateien brauchen das Werkzeug nicht: ein WAV ist ein WAV, "
                  "und die Liste ist Text. Wer später ein anderes Gerät oder "
                  "Programm benutzt, kann damit weiterarbeiten. Die Karte selbst "
                  "wurde nur gelesen, nicht verändert.") + "\n")
    return ziel, anzahl, ohne_ton


def _pruefe_herausgabe(basis, temp):
    """Herausgabe auf der Testkarte prüfen: Dateien und Liste müssen stimmen."""
    ordner, anzahl, _ohne = herausgeben(basis, os.path.join(temp, "herausgabe"))
    wavs = [n for n in os.listdir(ordner) if n.lower().endswith(".wav")]
    if len(wavs) != anzahl:
        raise SystemExit(T("Herausgabe: %d WAV-Dateien gezählt, %d erwartet")
                         % (len(wavs), anzahl))
    liste = os.path.join(ordner, "karte.csv")
    if not os.path.exists(liste):
        raise SystemExit(T("Herausgabe: karte.csv fehlt"))
    with open(liste, encoding="utf-8-sig") as f:
        zeilen = [z for z in f.read().splitlines() if z.strip()]
    if len(zeilen) != anzahl + 1:
        raise SystemExit(T("Herausgabe: %d Zeilen in der Liste, %d erwartet")
                         % (len(zeilen), anzahl + 1))
    if not os.path.exists(os.path.join(ordner, "LIESMICH.txt")):
        raise SystemExit(T("Herausgabe: LIESMICH.txt fehlt"))


def struktur_bericht(basis):
    """Einen kleinen Bericht über den Kartenaufbau schreiben (ohne Musik).

    Anlaß (20.11.2026): Für ein unbekanntes Pedal braucht es nur Ordnernamen und
    XML-Gerüst, um den Aufbau zu belegen. Der Bericht enthält:
      · die Ordnerstruktur bis zur Ebene der Patch-Ordner
      · die XML-Dateien, aber mit leeren Textwerten (Namen und Beschreibungen
        des Nutzers bleiben draußen)
      · KEINE Audiodateien
    Abgelegt wird er in ~/JamMan-Strukturbericht/ — abgeschickt wird er nur vom
    Nutzer selbst.
    """
    import zipfile
    ziel_ordner = os.path.join(os.path.expanduser("~"), "JamMan-Strukturbericht")
    os.makedirs(ziel_ordner, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ziel = os.path.join(ziel_ordner, "struktur_%s_%s.zip"
                        % (os.path.basename(basis.rstrip("/")), stamp))
    import re as _re

    def entschaerfen(text):
        """Textwerte leeren, Feldnamen und kurze Werte (Zahlen) behalten."""
        def ersetze(treffer):
            wert = treffer.group(3).strip()
            if len(wert) <= 3:          # 1, 0, 120 — technische Angaben
                return treffer.group(0)
            return treffer.group(1) + "…" + treffer.group(4)
        return _re.sub("(<([A-Za-z_][A-Za-z0-9_]*)[^>]*>)([^<]{1,200})(</[A-Za-z_][A-Za-z0-9_]*>)",
                       ersetze, text)

    seiten = []
    anzahl_xml = 0
    for ordner, unterordner, dateien in os.walk(basis):
        unterordner[:] = [u for u in unterordner if not u.startswith(".")][:8]
        rel = os.path.relpath(ordner, basis)
        if rel.count(os.sep) > 2:
            unterordner[:] = []
            continue
        for datei in sorted(dateien):
            if not datei.lower().endswith(".xml"):
                continue
            pfad = os.path.join(ordner, datei)
            try:
                with open(pfad, encoding="utf-8", errors="replace") as f:
                    inhalt = f.read(4000)
                seiten.append((os.path.relpath(pfad, basis).replace(os.sep, "/"),
                               entschaerfen(inhalt)))
                anzahl_xml += 1
            except OSError:
                continue
            if anzahl_xml >= 6:
                break
        if anzahl_xml >= 6:
            break
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
        kopf = ["Geräteordner: %s" % os.path.basename(basis.rstrip("/")),
                "Aufgenommen: %s" % datetime.now().strftime("%d.%m.%Y %H:%M"),
                "Werkzeug: %s" % FASSUNG,
                "",
                "Enthalten sind Ordnernamen und XML-Gerüst. Textwerte (Patch-Namen,",
                "Beschreibungen) sind durch … ersetzt. Keine Audiodateien.",
                "",
                "Ordner:"]
        for ordner, unterordner, dateien in os.walk(basis):
            rel = os.path.relpath(ordner, basis)
            if rel.count(os.sep) > 1:
                unterordner[:] = []
                continue
            kopf.append("  " + ("." if rel == "." else rel) + "/")
            for name in sorted(dateien)[:12]:
                if name.lower().endswith((".xml", ".txt")):
                    kopf.append("      " + name)
            if rel.count(os.sep) >= 1:
                unterordner[:] = []
        z.writestr("LIESMICH.txt", "\n".join(kopf) + "\n")
        for name, inhalt in seiten:
            z.writestr("xml/" + name, inhalt)
    return ziel, anzahl_xml


def unbekanntes_geraet(pfad):
    """Ordner, der wie ein JamMan aussieht, aber nicht in KARTENORDNER steht.

    Anlaß (20.11.2026): Statt fremde Karten abzuweisen, kann das Werkzeug sie
    LESEN und den Aufbau melden — so wird aus jedem Besitzer eines unbekannten
    Pedals ein möglicher Beleg. Geschrieben wird nichts.
    """
    p = os.path.abspath(os.path.expanduser(pfad))
    if not ist_ordner(p):
        return None
    # direkt der Geräteordner gewählt?
    if os.path.basename(p).lower().startswith("jamman"):
        if os.path.basename(p).lower() not in [w.lower() for w in KARTENORDNER]:
            return os.path.basename(p)
    kandidaten = []
    try:
        for name in os.listdir(p):
            if not name.lower().startswith("jamman"):
                continue
            if name.lower() in [w.lower() for w in KARTENORDNER]:
                continue
            q = os.path.join(p, name)
            if not ist_ordner(q):
                continue
            # muß wenigstens einen Patch-Ordner enthalten
            try:
                if any(n.lower().startswith("patch") for n in os.listdir(q)):
                    kandidaten.append(name)
            except OSError:
                continue
    except OSError:
        return None
    return kandidaten[0] if kandidaten else None


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
    # Unbekanntes Gerät? Dann lesen, aber nicht schreiben.
    fremd = unbekanntes_geraet(p)
    if fremd:
        global NUR_LESEN
        NUR_LESEN = True
        UNTERORDNER = fremd
        return os.path.join(p, fremd) if os.path.basename(p).lower() != fremd.lower() else p
    raise SystemExit(T("Fehler: auf '%s' liegt kein unterstützter "
                         "JamMan-Geräteordner (%s). Das ist keine JamMan-Karte.")
                     % (p, ", ".join(KARTENORDNER)))


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


def patch_beschreibung(pfad):
    """Die Beschreibungsfelder eines Patches lesen.

    Rückgabe: dict mit name, artist, genre, rating, tags, description, bpm.
    Was fehlt, kommt als leerer Text zurück; bpm als Zahl oder None.
    """
    werte = {"name": "", "artist": "", "genre": "", "rating": "",
             "tags": "", "description": "", "bpm": None}
    text, ptext = "", ""
    try:
        text = open(os.path.join(pfad, "patch.xml"), encoding="utf-8",
                    errors="replace").read()
    except OSError:
        pass
    try:
        ptext = open(os.path.join(pfad, "PhraseA", "phrase.xml"),
                     encoding="utf-8", errors="replace").read()
    except OSError:
        pass
    for feld, tag in (("name", "PatchName"), ("artist", "Artist"),
                      ("genre", "Genre"), ("rating", "Rating"),
                      ("tags", "Tags"), ("description", "Description")):
        m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), text)
        if m:
            werte[feld] = xml_entpacken(m.group(1).strip())
    m = re.search(r"<BeatsPerMinute>([^<]*)</BeatsPerMinute>", ptext)
    if m:
        try:
            werte["bpm"] = float(m.group(1))
        except ValueError:
            pass
    return werte


def xml_entpacken(text):
    """&amp; → & usw. (die XML-Dateien des Pedals kodieren Sonderzeichen)."""
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&"))


def patch_beschreibung_setzen(pfad, werte):
    """Beschreibungsfelder schreiben; was nicht in werte steht, bleibt.

    Rückgabe: Felder, die es im XML noch nicht gab (dann wurden sie
    eingefügt).
    """
    def kodiert(text):
        return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    px = os.path.join(pfad, "patch.xml")
    ph = os.path.join(pfad, "PhraseA", "phrase.xml")
    if not os.path.exists(px):
        raise RuntimeError(T("Die Datei %s fehlt.") % px)

    fehlend = []
    text = open(px, encoding="utf-8", errors="replace").read()
    for feld, tag in (("name", "PatchName"), ("artist", "Artist"),
                      ("genre", "Genre"), ("rating", "Rating"),
                      ("tags", "Tags"), ("description", "Description")):
        if feld not in werte:
            continue
        wert = kodiert(werte[feld])
        # Leere Felder stehen im Pedal-XML als <Description /> — das wird
        # zuerst ersetzt, sonst bliebe der alte Inhalt stehen.
        leer = r"<%s\s*/>" % tag
        if re.search(leer, text):
            text = re.sub(leer, "<%s>%s</%s>" % (tag, wert, tag), text, count=1)
            continue
        text, da = setze_wert(text, tag, wert)
        if not da:
            fehlend.append(tag)
    open(px, "w", encoding="utf-8").write(text)

    if werte.get("bpm") is not None and os.path.exists(ph):
        ptext = open(ph, encoding="utf-8", errors="replace").read()
        ptext, da = setze_wert(ptext, "BeatsPerMinute",
                               "%.10f" % float(werte["bpm"]))
        if not da:
            fehlend.append("BeatsPerMinute")
        ptext, _ = setze_wert(ptext, "BpmValidated", "1")
        open(ph, "w", encoding="utf-8").write(ptext)
    return fehlend


def patch_einstellungen(pfad):
    """Stoppart, Rhythmus, Wiedergabe, rückwärts und Tempo eines Patches.

    Rückgabe: (stop, rhy, loop, rueck, bpm) — rueck ist True/False, bpm eine
    Zahl oder None. Am 18.11.2026 ergänzt: rückwärts (IsReversed) und Tempo
    wurden gelesen, aber nirgends gezeigt.
    """
    stop, rhy, loop = "?", "?", "?"
    rueck, bpm = False, None
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
            rhy = {"Silence": "ohne", "StudioKickAndHighHat": "klick"}.get(
                m.group(1), m.group(1))
    except OSError:
        pass
    try:
        t = open(ph, encoding="utf-8", errors="replace").read()
        m = re.search(r"<IsLoop>([^<]*)", t)
        if m:
            loop = "loop" if m.group(1) == "1" else "single"
        m = re.search(r"<IsReversed>([^<]*)", t)
        if m:
            rueck = m.group(1).strip() == "1"
        m = re.search(r"<BeatsPerMinute>([^<]*)", t)
        if m:
            try:
                bpm = float(m.group(1))
            except ValueError:
                bpm = None
    except OSError:
        pass
    return stop, rhy, loop, rueck, bpm


class _Zeit:
    """Mißt eine Arbeitsstufe und schreibt sie ins Fehlerprotokoll.

    Anlaß (19.11.2026): Beim Umkehren und Zurücknehmen erschien ein
    „Beachball", obwohl die Datei kurz ist. Ohne Messung ist nicht zu sagen,
    welcher Schritt hängt — also wird jeder Schritt mit Dauer protokolliert.
    """

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        import time as _zeit
        self.t0 = _zeit.time()
        return self

    def __exit__(self, art, wert, spur):
        import time as _zeit
        dauer = _zeit.time() - self.t0
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "jamman-fehler.log"), "a", encoding="utf-8") as f:
                f.write("%s  [Zeit] %-28s %.2f s\n"
                        % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           self.name, dauer))
        except OSError:
            pass
        return False


def wav_rueckwaerts(pfad, ziel=None):
    """Eine WAV rückwärts schreiben und den Pfad zurückgeben.

    Das Pedal spielt rückwärts, weil in seiner phrase.xml IsReversed steht.
    Das Fenster spielte die Datei selbst ab — dort hörte man davon nichts
    (Rückmeldung 18.11.2026). Mit ffmpeg (areverse) entsteht eine Hörprobe;
    ohne ffmpeg bleibt es beim Original.
    """
    import tempfile

    if not werkzeug_pfad("ffmpeg"):
        return None
    # Zwischenspeicher: dieselbe Datei wird beim zweiten Anhören nicht erneut
    # umgerechnet (19.11.2026). Der Schlüssel enthält Pfad, Größe und
    # Änderungszeit; ändert sich die Datei, entsteht ein neuer Eintrag.
    try:
        st = os.stat(pfad)
        kennung = "%s|%d|%d" % (os.path.abspath(pfad), st.st_size, int(st.st_mtime))
    except OSError:
        kennung = os.path.abspath(pfad)
    ordner = os.path.join(tempfile.gettempdir(), "jamman-rueckwaerts")
    try:
        os.makedirs(ordner, exist_ok=True)
    except OSError:
        ordner = tempfile.mkdtemp()
    import hashlib
    zwischen = os.path.join(ordner, hashlib.sha1(kennung.encode()).hexdigest()[:16] + ".wav")
    if ziel is None and os.path.exists(zwischen):
        return zwischen
    # Aufräumen: höchstens 40 Hörproben liegen lassen (19.11.2026).
    try:
        vorhanden = [os.path.join(ordner, x) for x in os.listdir(ordner)
                     if x.endswith(".wav")]
        if len(vorhanden) > 40:
            vorhanden.sort(key=lambda f: os.path.getmtime(f))
            for alt_datei in vorhanden[:len(vorhanden) - 40]:
                try:
                    os.remove(alt_datei)
                except OSError:
                    pass
    except OSError:
        pass
    ziel = ziel or zwischen
    with _Zeit("ffmpeg areverse"):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", pfad,
                            "-af", "areverse", ziel],
                           capture_output=True)
    if r.returncode != 0 or not os.path.exists(ziel):
        return None
    return ziel


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
    for w, _d, ds in os.walk(os.path.dirname(basis)):
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
    print(T("  %-10s %-34s %8s  %s") % (T("Ordner"), T("Name"), T("Dauer"), T("WAV-Format")))
    for _nummer, name, pfad in alle:
        w = phrase_wav(pfad)
        if w:
            dauer, rate, kanaele, bits = wav_info(w)
            format_text = T("%d Hz / %d Bit / %d K") % (rate, bits, kanaele)
            ok = (rate, bits, kanaele) == WOLL
            print(T("  %-10s %-34s %8s  %s%s%s") % (
                name, patch_name(pfad)[:34], sekunden(dauer), format_text,
                "" if ok else T("   ← abweichend"),
                T("   ← über 10 min, Pedal nimmt sie nicht an")
                if dauer > MAX_LOOP_SEKUNDEN else ""))
        else:
            print(T("  %-10s %-34s %8s  %s") % (name, patch_name(pfad)[:34], "—", T("keine WAV")))
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
    for wurzel, _ordner, dateien in os.walk(basis):
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


# Alle erzeugten Balken, damit ein Prüfer sie einzeln nachsehen kann
# (19.11.2026: Der Selbsttest zählte einen Balken als „offen", dessen Fenster mit
# dem Hauptfenster längst zerstört war). Steht auf Modulebene, weil eine Liste
# als Klassenattribut von allen Instanzen geteilt würde — das war gewollt, ist
# aber als Klassenattribut leicht zu übersehen (ruff: RUF012).
_BALKEN = []


class Fortschritt:
    """Kleines Fenster mit Balken für lange Kopier- und Löschvorgänge.

    Anlaß (19.11.2026, Wunsch des Nutzers): Beim Sichern einer vollen Karte
    oder beim Retten von 797 MB stand das Werkzeug still, ohne zu sagen, wie
    weit es ist. Der Balken zeigt Anteil und Dateizahl; die Arbeit läuft
    weiterhin im Hauptfaden, damit nichts verklemmt — der Balken wird bei
    jedem Schritt neu gezeichnet.
    """

    BREITE = 380
    HOEHE = 12

    offen = 0

    def __init__(self, haupfenster, titel):
        import tkinter as tk
        self.fenster = None
        self.anteil = None
        self.titel = titel
        self.haupfenster = haupfenster
        self._tk = tk
        _BALKEN.append(self)

    def _bauen(self):
        import tkinter as tk
        if self.fenster is not None:
            return
        f = tk.Toplevel(self.haupfenster)
        # Ohne diese Zeile blieb self.fenster leer, und JEDER setzen-Aufruf
        # baute ein neues Fenster — bei drei Meldungen standen drei Balken
        # übereinander und keiner ging zu (19.11.2026 gesehen).
        self.fenster = f
        f.title(self.titel)
        f.configure(bg=FARBE_HINTERGRUND)
        f.resizable(False, False)
        try:
            f.attributes("-topmost", True)
        except Exception:
            pass
        self.text = tk.StringVar(value=T("einen Moment …"))
        tk.Label(f, textvariable=self.text, bg=FARBE_HINTERGRUND,
                 fg=FARBE_SCHRIFT, font=schrift(11), anchor="w",
                 width=46).pack(padx=16, pady=(14, 6))
        self.leinwand = tk.Canvas(f, width=self.BREITE, height=self.HOEHE,
                                  bg=FARBE_LISTE, highlightthickness=0)
        self.leinwand.pack(padx=16, pady=(0, 14))
        self.balken = self.leinwand.create_rectangle(
            0, 0, 0, self.HOEHE, fill=FARBE_AUSWAHL, width=0)
        Fortschritt.offen += 1
        f.update()

    def kriechen(self, ziel):
        """Balken langsam in Richtung ziel bewegen (ohne Meldung).

        Anlaß (19.11.2026): Zwischen zwei echten Meldungen stand der Balken
        still, und es sah aus, als passiere nichts. Solange gearbeitet wird,
        wandert er sichtbar weiter — bis höchstens kurz vor das Ziel, damit er
        keine Fertigstellung vortäuscht.
        """
        try:
            if self.fenster is None or self.anteil is None:
                return
            grenze = max(0.0, min(0.95, ziel - 0.02))
            if self.anteil < grenze:
                self.anteil = min(grenze, self.anteil + 0.01)
                self.leinwand.coords(self.balken, 0, 0,
                                     self.BREITE * self.anteil, self.HOEHE)
                self.fenster.update()
        except Exception:
            pass

    def setzen(self, anteil, text=""):
        try:
            self._bauen()
            self.anteil = max(0.0, min(1.0, float(anteil)))
            self.leinwand.coords(self.balken, 0, 0,
                                 self.BREITE * self.anteil, self.HOEHE)
            if text:
                self.text.set(text)
            self.fenster.update()
        except Exception:
            pass

    def schliessen(self):
        try:
            if self.fenster is not None:
                self.fenster.destroy()
                Fortschritt.offen = max(0, Fortschritt.offen - 1)
        except Exception:
            pass
        self.fenster = None
        try:
            if self in _BALKEN:
                _BALKEN.remove(self)
        except Exception:
            pass


def fuehre_im_hintergrund(haupfenster, titel, arbeit, starttext=None):
    """Schwere Dateiarbeit in einem Arbeitsfaden, Balken im Hauptfaden.

    Anlaß (19.11.2026): Beim Anwenden von Einstellungen stand das Fenster still
    (Beachball), weil Lesen und Schreiben der Aufnahme im Hauptfaden liefen.
    Der Arbeitsfaden rührt Tk nicht an — er legt nur Zahlen und Texte in eine
    Warteschlange, die der Hauptfaden abholt und in den Balken schreibt.
    """
    import queue
    import threading
    import time as _zeit

    # Balken erst zeigen, wenn es dauert (19.11.2026): Bei einem reinen
    # Einstellungswechsel ist die Arbeit in Millisekunden fertig — ein Fenster,
    # das kurz aufblitzt und wieder verschwindet, verwirrt mehr als es hilft.
    VERZOEGERUNG = 0.35
    balken = None
    beginn = _zeit.time()
    schlange = queue.Queue()
    ergebnis = {}

    def lauf():
        _HINTERGRUND.aktiv = True
        try:
            ergebnis["wert"] = arbeit(lambda a, t: schlange.put((a, t)))
        except BaseException as fehler:      # auch SystemExit weiterreichen
            ergebnis["fehler"] = fehler
        finally:
            _HINTERGRUND.aktiv = False
            schlange.put(("ende", ""))

    faden = threading.Thread(target=lauf, daemon=True)
    faden.start()
    try:
        while True:
            try:
                anteil, text = schlange.get_nowait()
            except queue.Empty:
                anteil, text = None, None
            if anteil == "ende":
                break
            if balken is None and _zeit.time() - beginn > VERZOEGERUNG:
                # Es dauert länger als einen Augenblick — jetzt zeigen.
                balken = Fortschritt(haupfenster, titel)
                balken.setzen(0.0, starttext or T("einen Moment …"))
            if anteil is not None and balken is not None:
                balken.ziel = anteil
                balken.setzen(anteil, text)
            elif balken is not None:
                balken.kriechen(getattr(balken, "ziel", 1.0))
            try:
                haupfenster.update()
            except Exception:
                break
            _zeit.sleep(0.02)
        faden.join()
    finally:
        if balken is not None:
            balken.schliessen()
    if "fehler" in ergebnis:
        raise ergebnis["fehler"]
    return ergebnis.get("wert")


def kopiere_baum(quelle, ziel, melden=None, titel=None):
    """Ordner mit Unterordnern kopieren und dabei den Fortschritt melden.

    Datei für Datei mit shutil.copy2 (Zeiten und Rechte bleiben), damit
    „12 von 340 Dateien" überhaupt möglich ist. Rückgabe: Anzahl kopierter
    Dateien und Bytes.
    """
    dateien = []
    for wurzel, _ordner, namen in os.walk(quelle):
        for name in namen:
            pfad = os.path.join(wurzel, name)
            try:
                dateien.append((pfad, os.path.relpath(pfad, quelle),
                                os.path.getsize(pfad)))
            except OSError:
                continue
    gesamt = sum(g for _, _, g in dateien) or 1
    erledigt_bytes = 0
    erledigt = 0
    for pfad, rel, groesse in dateien:
        ziel_pfad = os.path.join(ziel, rel)
        try:
            os.makedirs(os.path.dirname(ziel_pfad), exist_ok=True)
            shutil.copy2(pfad, ziel_pfad)
        except OSError:
            continue
        erledigt += 1
        erledigt_bytes += groesse
        if melden and (erledigt % 5 == 0 or erledigt == len(dateien)):
            melden(erledigt_bytes / float(gesamt),
                   T("%d von %d Dateien · %.0f MB")
                   % (erledigt, len(dateien), erledigt_bytes / 1048576.0))
    if not dateien:
        try:
            os.makedirs(ziel, exist_ok=True)
        except OSError:
            pass
    return erledigt, erledigt_bytes


def befehl_sichern(basis, ziel, schreiben, melden=None):
    ziel = os.path.abspath(os.path.expanduser(ziel))
    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M")
    zielordner = os.path.join(ziel, "JamManSicherung_" + stempel)
    print(T("  Quelle: %s") % basis)
    print(T("  Ziel:   %s") % zielordner)
    if not schreiben:
        print(T("  Trockenlauf — nichts kopiert. Mit --schreiben ausführen."))
        return
    os.makedirs(zielordner, exist_ok=True)
    _, groesse = kopiere_baum(basis, os.path.join(zielordner, UNTERORDNER), melden)
    anzahl = len(patches(basis))
    if melden:
        melden(1.0, T("fertig"))
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
    benutzt = {n for n, _, _ in alle}
    breiten = Counter(len(name) - 5 for _, name, _ in alle)
    breite = breiten.most_common(1)[0][0] if breiten else 2
    frei = 1
    while frei in benutzt:
        frei += 1
    return "%0*d" % (breite, frei)


def vorlage_finden(basis):
    """Ein vollständiger Patch dient als Vorlage für patch.xml und phrase.xml."""
    for _nummer, _name, pfad in patches(basis):
        if os.path.exists(os.path.join(pfad, "patch.xml")) and \
           os.path.exists(os.path.join(pfad, "PhraseA", "phrase.xml")):
            return pfad
    return None


def wiedergabe_rueckwaerts(wiedergabe):
    """Soll rückwärts gespielt werden? (IsReversed im phrase.xml)"""
    return "1" if wiedergabe and ("↺" in wiedergabe
                            or "rückwärts" in wiedergabe) else "0"


def setze_wert(text, tag, wert):
    """Ersetzt <tag>…</tag>. Fehlt das Feld in der Vorlage, wird es
    eingefügt - sonst bliebe die gewünschte Einstellung wirkungslos."""
    muster = r"<%s>.*?</%s>" % (tag, tag)
    if re.search(muster, text, flags=re.DOTALL):
        return re.sub(muster, "<%s>%s</%s>" % (tag, wert, tag),
                      text, count=1, flags=re.DOTALL), True
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


def finde_schnittpunkte(pfad, anzahl, fenster=90.0, min_pause=0.25,
                        schwelle=0.35, alle_pausen=False):
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
    ruhig = [(tm, v) for tm, v in h if v < mittel * schwelle]

    # Gruppen ruhiger Stellen bilden
    gruppen = []
    for tm, v in ruhig:
        if gruppen and tm - gruppen[-1][-1][0] <= schritt * 3:
            gruppen[-1].append((tm, v))
        else:
            gruppen.append([(tm, v)])
    pausen = [(g[0][0], g[-1][0] + laenge) for g in gruppen
              if (g[-1][0] + laenge) - g[0][0] >= min_pause]

    if alle_pausen:
        # Alle gefundenen Pausen als Schnittstellen: die Anzahl der Teile
        # ergibt sich aus der Aufnahme. Geschnitten wird am Ende der Pause.
        return ([bis for _, bis in pausen],
                ["Ende einer Pause"] * len(pausen),
                [(von, bis) for von, bis in pausen])

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


def schneide_bereich(quelle, ziel, von_sekunden, bis_sekunden, fade_ms=0.0):
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
    if fade_ms and fade_ms > 0 and daten:
        # Kurze Rampe an Anfang und Ende: verhindert Knackser, wenn der
        # Schnitt nicht auf einem Nulldurchgang liegt (21.11.2026).
        import array as _array
        rahmen = len(daten) // block
        n = int(rate * (fade_ms / 1000.0))
        if n > 0 and rahmen > 2 * n:
            a = _array.array("h")
            a.frombytes(daten)
            for k in range(n):
                faktor = k / float(n)
                for ch in range(kanaele):
                    a[k * kanaele + ch] = int(a[k * kanaele + ch] * faktor)
                    j = rahmen - 1 - k
                    a[j * kanaele + ch] = int(a[j * kanaele + ch] * faktor)
            daten = a.tobytes()
    with open(ziel, "wb") as z:
        kopf = b"RIFF" + struct.pack("<I", 36 + len(daten)) + b"WAVE"
        kopf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, kanaele, rate,
                                     rate * block, block, 16)
        kopf += b"data" + struct.pack("<I", len(daten))
        z.write(kopf); z.write(daten)
    return True


def befehl_teilen(basis, wav, name, schreiben, teile=None, laenge=None,
                  wurzel=None, stopp=None, wiedergabe=None, sinnvoll=True,
                  ohne_pause=True, marker=None, schwelle=0.35,
                  min_pause=0.25, fade_naht=0.0, alle_pausen=False):
    """Eine Datei in mehrere Patches aufteilen.

    Gedacht für Aufnahmen über 10 Minuten, die das Pedal sonst nicht
    annimmt. Die Teile liegen sample-genau aneinander - kein Ton fehlt,
    keiner doppelt.
    """
    dauer = wav_info(wav)[0]
    if dauer <= 0:
        raise SystemExit(T("Fehler: '%s' ist keine lesbare Audiodatei.") % wav)
    nur_schnittstellen = bool(marker) or alle_pausen
    if laenge and not nur_schnittstellen:
        anzahl = max(1, math.ceil(dauer / float(laenge)))
        stueck = float(laenge)
    elif teile and not nur_schnittstellen:
        anzahl = max(1, int(teile))
        stueck = dauer / float(anzahl)
    elif nur_schnittstellen:
        # Die Teile ergeben sich aus Marken oder Pausen - unten.
        anzahl = 1
        stueck = dauer
    else:
        raise SystemExit(T("Fehler: --teile N oder --laenge SEKUNDEN angeben."))
    if stueck > MAX_LOOP_SEKUNDEN and not nur_schnittstellen:
        raise SystemExit(T("Fehler: %.1f Minuten pro Teil sind zu lang "
                         "(Grenze %d Minuten).") % (stueck / 60.0, MAX_LOOP_SEKUNDEN //
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
        raise SystemExit(T("Fehler: Datei ließ sich nicht umwandeln."))
    dauer = wav_info(ganz)[0]

    mitteilen = []
    marken = sorted(float(m) for m in marker) if marker else []
    if marken:
        punkte = [m for m in marken if 0.0 < m < dauer]
        anzahl = len(punkte) + 1
        gruende = ["Marke"] * len(punkte)
        paare = [None] * len(punkte)
        print("  " + T("Es wird an %d gesetzten Marken geschnitten.") % len(punkte))
    elif alle_pausen:
        punkte, gruende, paare = finde_schnittpunkte(
            ganz, 0, fenster=max(90.0, stueck / 4), min_pause=min_pause,
            schwelle=schwelle, alle_pausen=True)
        anzahl = len(punkte) + 1
        print("  " + T("Es wird an allen %d gefundenen Pausen geschnitten.")
              % len(punkte))
    else:
        punkte = None
    if punkte is not None or (sinnvoll and anzahl > 1):
        if punkte is None:
            print(T("  Suche musikalisch brauchbare Schnittstellen …"))
            punkte, gruende, paare = finde_schnittpunkte(ganz, anzahl,
                                                         fenster=max(90.0, stueck/4),
                                                         min_pause=min_pause,
                                                         schwelle=schwelle)
        if len(punkte) != anzahl - 1:
            print(T("  Hinweis: keine brauchbaren Pausen gefunden (%s) -")
                  % (gruende[0] if gruende else "kein Ergebnis"))
            print(T("           es wird gleichmäßig geschnitten."))
            punkte = [dauer * k / float(anzahl) for k in range(1, anzahl)]
            gruende = ["rechnerisch"] * len(punkte)
            paare = [None] * len(punkte)
        grenzen = [0.0, *punkte, dauer]
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
            if any(p is not None for p in paare):
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
            if not schneide_bereich(ganz, teil, von, bis, fade_naht):
                raise SystemExit(T("Fehler: Teil %d ließ sich nicht schneiden." )% i)
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
        raise SystemExit(T("Fehler: '%s' gibt es nicht.") % wav)
    dauer, rate, kanaele, bits = wav_info(wav)
    if dauer <= 0:
        raise SystemExit(T("Fehler: '%s' ist keine lesbare Audiodatei.%s")
                         % (wav, unlesbar_hinweis(wav)))
    ziel_laenge = None
    if dauer > MAX_LOOP_SEKUNDEN:
        if laenge:
            ziel_laenge = min(laenge, float(MAX_LOOP_SEKUNDEN))
        elif kuerzen:
            ziel_laenge = float(MAX_LOOP_SEKUNDEN) - 1.0     # 9:59, mit Luft
        elif not trotzdem:
            raise SystemExit(
                T("Fehler: Die Datei ist %.1f Minuten lang.\n"
                "  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n"
                "  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n"
                "  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n"
                "  nicht erkannt - und können dabei verschwinden.\n\n"
                "  So geht es weiter:\n"
                "    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n"
                "                         12 ms ausgeblendet, damit es nicht knackt)\n"
                "    --laenge 480         auf 8:00 kürzen\n"
                "    --trotzdem           unverändert schreiben (zum Ausprobieren)")
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
        raise SystemExit(T("Fehler: kein vollständiger Patch als Vorlage gefunden."))
    if os.path.exists(zielpfad):
        raise SystemExit(T("Fehler: '%s' liegt schon vor.") % zielpfad)
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
                T("Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n"
                "  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n"
                "  ffmpeg installieren.") % (rate, kanaele))

    if ziel_laenge:
        if kuerze_wav(ziel_wav, ziel_laenge):
            print(T("  Kürzen:       auf %s geschnitten (harter Schnitt, "
                  "12 ms ausgeblendet)") % sekunden(ziel_laenge))
        else:
            raise SystemExit(T("Fehler: Die Datei ließ sich nicht kürzen."))

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
        raise SystemExit(T("Fehler: Die Umwandlung hat keine hörbare Aufnahme\n"
                         "  ergeben (nur Stille). Datei prüfen: %s" )% os.path.basename(wav))

    if fade and not fade_ausblenden(ziel_wav, fade):
        print(T("  Hinweis: Fade konnte nicht eingerechnet werden."))
    if normieren or gain:
        ergebnis = (pegel_anpassen(ziel_wav, ziel_spitze_db=-1.0) if normieren
                    else pegel_anpassen(ziel_wav, db=gain, begrenzen=begrenzen))
        if ergebnis:
            print_pegel(ergebnis)
            if ergebnis.get("nachher") is not None:
                schreibe_pegelprotokoll(basis, [{
                    "ordner": os.path.basename(zielpfad), "name": patch_name(zielpfad),
                    "faktor": 10 ** ((ergebnis["nachher"] - ergebnis["vorher"]) / 20.0),
                    "vorher": ergebnis["vorher"], "nachher": ergebnis["nachher"]}],
                    "pegel", basis)
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
                  text, count=1, flags=re.DOTALL)
    text = re.sub(r"<ID>.*?</ID>", "<ID>%s</ID>" % uuid.uuid4(), text, count=1, flags=re.DOTALL)
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
        raise SystemExit(T("Fehler: '%s' lässt sich nicht schreiben.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>") % ordner)
    return os.path.dirname(ziel)


def befehl_aendern(basis, nummer, schreiben, stopp=None, rhythmus=None,
                   wiedergabe=None, name=None, wurzel=None, fade=None,
                   gain=None, normieren=False, begrenzen=True, melden=None):
    """Einstellungen eines vorhandenen Patches ändern."""
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % nummer)
    pfad = alle[nummer]
    print(T("  Patch:        %s  (%s)") % (os.path.basename(pfad), patch_name(pfad)))
    if not (stopp or rhythmus or wiedergabe or name or fade or gain or normieren):
        raise SystemExit(T("Fehler: nichts angegeben (--stopp, --rhythmus, "
                         "--wiedergabe, --name oder --fade)."))
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

    if melden:
        melden(0.05, T("Sicherung anlegen …"))
    if fade or gain or normieren:
        ziel = sicher_ablegen(basis, [pfad], "vor_pegel", wurzel)
    else:
        ziel = sicherung_der_einstellungen(basis, pfad, "vor_aendern", wurzel)
    if melden:
        melden(0.35, T("Einstellungen schreiben …"))
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
        ptext, _ = setze_wert(ptext, "IsReversed",
                               wiedergabe_rueckwaerts(wiedergabe))
        open(ph, "w", encoding="utf-8").write(ptext)

    if fade or gain or normieren:
        if melden:
            melden(0.50, T("Aufnahme bearbeiten …"))
        wav = os.path.join(pfad, "PhraseA", "phrase.wav")
        if not os.path.exists(wav):
            raise SystemExit(T("Fehler: Patch%d hat keine phrase.wav.") % nummer)
        if normalisiere_wav(wav):
            print(T("  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben"))
        if fade and not fade_ausblenden(wav, fade):
            raise SystemExit(T("Fehler: Die Aufnahme ließ sich nicht ausblenden "
                             "(kein 16-Bit-WAV?)."))
        if gain or normieren:
            ergebnis = (pegel_anpassen(wav, ziel_spitze_db=-1.0) if normieren
                        else pegel_anpassen(wav, db=gain, begrenzen=begrenzen))
            if not ergebnis:
                raise SystemExit(T("Fehler: Der Pegel ließ sich nicht ändern."))
            print_pegel(ergebnis)

    saeubere_karte(basis)
    if melden:
        melden(1.0, T("fertig"))
    print(T("  Geändert. Sicherung: %s") % ziel)


def befehl_auswerfen(karte):
    """Karte auswerfen. Vorher werden die Apple-Dateien entfernt."""
    ziel = volumen_pfad(karte)
    if not os.path.isdir(ziel):
        raise SystemExit(T("Fehler: '%s' ist nicht eingelegt.") % karte)
    try:
        saeubere_karte(os.path.join(ziel, UNTERORDNER))
    except Exception:
        pass
    erfolg, meldung = auswerfen_datentraeger(karte)
    if erfolg:
        print(T("  %s Sie kann jetzt abgezogen werden.") % meldung)
    else:
        raise SystemExit(T("Fehler beim Auswerfen: %s") % meldung)


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
    for _nummer, name, _ in alle:
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
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % nummer)
    w = phrase_wav(alle[nummer])
    if not w:
        raise SystemExit(T("Fehler: Patch%d hat keine WAV.") % nummer)
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
    "struktur": ("Report the card structure", "Kartenaufbau melden"),
    "herausgeben": ("Export the card contents", "Karte herausgeben"),
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
    "aktualisieren": ("Download the newest version", "Neueste Fassung holen"),
    "speicherabbild": ("Write an inventory of the pedal (read only)",
                       "Speicherabbild des Pedals schreiben (nur lesend)"),
    "vergleich": ("Compare two inventories", "Zwei Speicherabbilder vergleichen"),
    "firmware-holen": ("Fetch the pedal's firmware files (read only)",
                       "Firmware-Dateien des Pedals holen (nur lesend)"),
    "firmware-schreiben": ("Write firmware files into the pedal",
                           "Firmware-Dateien ins Pedal schreiben"),
    "jamlists": ("List the JamLists in a JamManager library",
                 "JamLists einer JamManager-Bibliothek auflisten"),
    "jamlist-holen": ("JamList as text setlist", "JamList als Textdatei holen"),
    "jamlist-schreiben": ("Text setlist as JamList", "Textdatei als JamList ablegen"),
    "gui": ("Open the window", "Fenster öffnen"),
    "menu": ("Text menu", "Textmenü"),
    "selbsttest": ("Report what the tool sees", "Bericht, was das Werkzeug sieht"),
    "funktionstest": ("Check the tool on this computer (buttons, file commands)",
                      "Werkzeug auf diesem Rechner prüfen (Knöpfe, Dateibefehle)"),
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
        'DATEIEN':
         'FILES',
        'Funktionstest in allen fünf Sprachen':
         'self-test in all five languages',
        'Dauer und Einstellungen kommen aus Dateigröße und WAV-Kopf — der Ton selbst wird dafür nicht übertragen.':
         'Duration and settings appear only after fetching — over USB only the name is read.',
        'Marken außerhalb der Datei wurden verworfen.':
         'Markers outside the file were dropped.',
        '%d Teile: %s':
         '%d parts: %s',
        '%d Teile, Schnitt an Pausen gesucht':
         '%d parts, cuts looked for at pauses',
        '%d Teile, gleichmäßig geschnitten':
         '%d parts, split evenly',
        'in %d Patches geteilt':
         'split into %d patches',
        'Marken':
         'Markers',
        'Teilen':
         'Split',
        'Alle Marken löschen':
         'Clear all markers',
        'Alle Pausen als Marken':
         'All pauses as markers',
        'Pausen gefunden: %d':
         'Pauses found: %d',
        'Keine Pause gefunden.':
         'No pause found.',
        'Wellenform nicht lesbar.':
         'Waveform cannot be read.',
        '%s   •   Klick setzt eine Marke, Rechtsklick entfernt sie':
         '%s   •   click sets a marker, right click removes it',
        'Schwelle %':
         'Threshold %',
        'Pause s':
         'Pause s',
        'Fade ms':
         'Fade ms',
        'Marken bitte als Zahlen mit Komma, z. B. 60, 120.':
         'Enter markers as numbers separated by commas, e.g. 60, 120.',
        'Schwelle, Pause und Fade bitte als Zahlen eintragen.':
         'Enter threshold, pause and fade as numbers.',
        'Schwelle bitte als Zahl zwischen 1 und 99.':
         'Threshold must be a number between 1 and 99.',
        'Pause bitte als Zahl zwischen 0,01 und 30 Sekunden.':
         'Pause must be a number between 0.01 and 30 seconds.',
        'Fade bitte als Zahl zwischen 0 und 50.':
         'Fade must be a number between 0 and 50.',
        'Es wird an %d gesetzten Marken geschnitten.':
         'Cutting at %d markers.',
        'Es wird an allen %d gefundenen Pausen geschnitten.':
         'Cutting at all %d pauses found.',
        'Schnittpunkt in Sekunden (mehrfach möglich)':
         'cut point in seconds (repeatable)',
        'an jeder gefundenen Pause trennen':
         'split at every pause found',
        'Ruhepegel für die Pausenerkennung in Prozent (Vorgabe 35)':
         'silence level for pause detection (default 35)',
        'kürzeste Pause, an der geschnitten wird (Vorgabe 0,25 s)':
         'shortest pause to cut at (default 0.25 s)',
        'Fade an jeder Naht in Millisekunden (Vorgabe 0)':
         'fade at every seam in milliseconds (default 0)',
        'Ende einer Pause':
         'end of a pause',
        'Marke':
         'marker',
        'Herausgeben':
         'Export',
        'Alles herausgeben …':
         'Export everything …',
        'Wohin herausgeben?':
         'Export to where?',
        'Erst eine Karte öffnen — dann läßt sie sich herausgeben.':
         'Open a card first — then it can be exported.',
        'Herausgegeben nach: %s':
         'Exported to: %s',
        '%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv und LIESMICH.txt.':
         '%d tracks as WAV, %d slots without audio, plus karte.csv and LIESMICH.txt.',
        'Die Karte wurde nur gelesen. Die Dateien brauchen dieses Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text.':
         'The card was only read. The files do not need this tool: a WAV is a WAV, and the list is plain text.',
        'Herausgabe einer JamMan-Karte':
         'Export of a JamMan card',
        'Datei':
         'File',
        'Gerät: %s':
         'Device: %s',
        'Aufgenommen: %s':
         'Recorded: %s',
        'Werkzeug: %s':
         'Tool: %s',
        'In diesem Ordner liegen %d Tonspuren als gewöhnliche WAV-Dateien, benannt nach ihrem Platz und ihrem Namen in der Karte. %d Plätze hatten keinen Ton.':
         'This folder holds %d tracks as ordinary WAV files, named after their slot and their name on the card. %d slots had no audio.',
        'karte.csv enthält zu jeder Spur: Platz, Name, Datei, Dauer, Stoppart, Wiedergabe, Tempo und Rhythmus. Semikolon als Trennzeichen, damit Excel und LibreOffice sie direkt öffnen.':
         'karte.csv lists for every track: slot, name, file, length, stop mode, playback, tempo and rhythm. Semicolon separated so Excel and LibreOffice open it directly.',
        'Diese Dateien brauchen das Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text. Wer später ein anderes Gerät oder Programm benutzt, kann damit weiterarbeiten. Die Karte selbst wurde nur gelesen, nicht verändert.':
         'These files do not need the tool: a WAV is a WAV, and the list is plain text. Whoever moves to another device or program later can keep working with them. The card itself was only read, never changed.',
        'Herausgabe: %d WAV-Dateien gezählt, %d erwartet':
         'Export: %d WAV files counted, %d expected',
        'Herausgabe: karte.csv fehlt':
         'Export: karte.csv is missing',
        'Herausgabe: %d Zeilen in der Liste, %d erwartet':
         'Export: %d lines in the list, %d expected',
        'Herausgabe: LIESMICH.txt fehlt':
         'Export: LIESMICH.txt is missing',
        'Nur-Lesen-Modus: „%s“ ist ein unbekanntes Gerät (%s). Der Kartenaufbau ist nicht belegt, deshalb wird nichts geschrieben. Lesen ist erlaubt (liste). Mit „jamman.py struktur <karte>“ entsteht ein Bericht, den Sie weitergeben können.':
         'Read-only mode: “%s” is an unknown device (%s). The card layout is not verified, so nothing is written. Reading is allowed (liste). “jamman.py struktur <card>” writes a report you can pass on.',
        'Speicherkarte des %s':
         'Memory card of the %s',
        'Struktur melden …':
         'Report structure …',
        'Strukturbericht':
         'Structure report',
        'Der Bericht ließ sich nicht schreiben: %s':
         'The report could not be written: %s',
        'Nur-Lesen-Modus: unbekanntes Gerät „%s“. Die Karte wird gelesen; geschrieben wird nichts, weil der Aufbau nicht belegt ist. Mit „Struktur melden …“ können Sie ihn belegen.':
         'Read-only mode: unknown device “%s”. The card is read; nothing is written because the layout is not verified. “Report structure …” provides that evidence.',
        'Bericht geschrieben: %s':
         'Report written: %s',
        'Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.':
         'Contains: folder names and the XML files, no audio and none of your text (patch names replaced by …). %d XML files.',
        'Bitte schicken Sie diese Datei an mich oder hängen Sie sie an ein GitHub-Issue — dann kann ich den Aufbau prüfen und das Gerät freigeben.':
         'Please send me this file or attach it to a GitHub issue — then I can check the layout and approve the device.',
        'Spalten (%s): %s':
         'Columns (%s): %s',
        'Spalten (%s): Aufbau unvollständig':
         'Columns (%s): layout incomplete',
        'Länge':
         'Length',
        'fade':
         'fade',
        'loop':
         'loop',
        'single':
         'single',
        "Fehler: auf '%s' liegt kein unterstützter JamMan-Geräteordner (%s). Das ist keine JamMan-Karte.":
         "  Error: '%s' contains no supported JamMan device folder (%s). That is not a JamMan card.",
        'beim Start automatisch nach Updates sehen':
         'check for updates automatically on start',
        'nach jedem Update automatisch prüfen':
         'check automatically after each update',
        'Aktualisierung':
         'Update',
        'Selbsttest nach Update':
         'Self test after update',
        'Fenster zu schmal für seinen Inhalt: %d von %d Pixel — bitte breiter ziehen':
         'Window too narrow for its content: %d of %d pixels — please widen it',
        'abgeschnittene Beschriftung: %s':
         'clipped label: %s',
        'Die letzte Sicherung stammt aus dem anderen Speicher. Bitte oben umschalten und es noch einmal versuchen.':
         'The last backup comes from the other storage. Please switch above and try again.',
        'deutscher Text in der Anzeige: %s':
         'German text left in the display: %s',
        'sofort':
         'immediately',
        '%s   •   %s   •   Klick in die Spur springt dorthin':
         '%s   •   %s   •   click on the track to jump there',
        'Per E-Mail senden':
         'Send by e-mail',
        'Mail im Mailprogramm vorbereitet — abschicken mußt du selbst.':
         'Mail prepared in your mail program — you have to send it yourself.',
        'Mailprogramm ließ sich nicht öffnen.':
         'The mail program did not open.',
        'Hier liegen einmal die Zusatzstarter und die Textdateien.\nDer Selbsttest im Werkzeug schreibt seinen Bericht dorthin, wo das Programm liegt: .jamman/.\n':
         'This folder holds the extra starters and the text files.\nThe self test writes its report where the program lives: .jamman/.\n',
        '  Ordner aufgeräumt — das Werkzeug startet neu …':
         '  Folder tidied up — the tool restarts …',
        'Diese Fassung liegt in einer anderen Ordneraufteilung. Bitte einmal von Hand herunterladen:\n%s':
         'This version uses a different folder layout. Please download it by hand once:\n%s',
        'Diese Fassung des Werkzeugs kann sich nicht selbst aktualisieren (neue Ordneraufteilung). Bitte einmal von Hand herunterladen:\n%s':
         'This version of the tool cannot update itself (new folder layout). Please download it by hand once:\n%s',
        'Als Issue vorbereiten':
         'Prepare as issue',
        'Issue im Browser vorbereitet — abschicken mußt du selbst.':
         'Issue prepared in the browser — you have to submit it yourself.',
        'Browser ließ sich nicht öffnen.':
         'The browser did not open.',
        'Nichts wird gesendet, solange du nicht selbst auf GitHub auf Absenden drückst.':
         'Nothing is sent unless you press Submit on GitHub yourself.',
        'Selbsttest':
         'Self test',
        'Selbsttest …':
         'Self test …',
        'Bericht des Selbsttests':
         'Self test report',
        'Kopieren':
         'Copy',
        'Kopieren ging nicht.':
         'Copying did not work.',
        'In die Zwischenablage kopiert.':
         'Copied to the clipboard.',
        'Das Werkzeug prüft sich selbst: es legt eine Testkarte an, drückt jeden Knopf und spielt die Dateibefehle durch.\n\nDabei gehen kurz Fenster auf und zu. Das Pedal bleibt unberührt, es wird nichts an deinen Karten geändert.\n\nDauert ein paar Sekunden. Fortfahren?':
         'The tool checks itself: it creates a test card, presses every button and runs the file commands.\n\nWindows open and close briefly. The pedal is not touched, nothing on your cards is changed.\n\nTakes a few seconds. Continue?',
        '  [Hinweis] Kein Fenster geprüft (%s): dieses Python hat Tk 8.5 — es lief das Textmenü.':
         '  [note] no window checked (%s): this Python has Tk 8.5 — the text menu ran.',
        '  Funktionstest des JamMan Card Tool':
         '  JamMan Card Tool self test',
        '  System:  %s':
         '  System:  %s',
        '  Python:  %s':
         '  Python:  %s',
        '  Fassung: %s':
         '  Version: %s',
        '  Testkarte: %s':
         '  Test card: %s',
        '  [ok]   Befehl %s':
         '  [ok]   command %s',
        '  [ok]   Befehl %s meldet: %s':
         '  [ok]   command %s reports: %s',
        '  [FEHL] Befehl %s: %s':
         '  [FAIL] command %s: %s',
        '  [Hinweis] Ohne Tk kein Fenstertest: %s':
         '  [note] without Tk there is no window test: %s',
        '  %d Befund(e) — bitte mitschicken (stehen auch in %s).':
         '  %d finding(s) — please send them along (they are also in %s).',
        '  Ergebnis: keine Befunde.':
         '  Result: nothing found.',
        '  [FEHL] Fenster (%s): %s':
         '  [FAIL] window (%s): %s',
        '  [ok]   Fenster (%s): %d Knöpfe, keine Ausnahme':
         '  [ok]   window (%s): %d buttons, no exception',
        '%d Balkenfenster blieben offen':
         '%d progress windows stayed open',
        'Fenster':
         'Window',
        'Abgebrochen — keine Datei geschrieben.':
         'Cancelled — no file written.',
        'Es läuft noch eine Arbeit. Abbrechen und das Fenster schließen?\n\nDie unfertige Datei wird gelöscht.':
         'A task is still running. Cancel it and close the window?\n\nThe unfinished file will be deleted.',
        '%d von %d Patches gelesen':
         '%d of %d patches read',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe aus dieser Sicherung zurückholen?\n\n  %s\n\nDie Aufnahme bleibt, wie sie ist. Der jetzige Stand wird vorher gesichert.':
         'Patch %d: bring name, stop mode, rhythm and playback back from this backup?\n\n  %s\n\nThe recording stays as it is. The current state is saved first.',
        'Patch%d aus dieser Sicherung wiederherstellen?\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen. Der jetzige Stand wird vorher gesichert.':
         'Restore patch %d from this backup?\n\n  %s\n\nThis can undo a fade, for example. The current state is saved first.',
        'Einstellungen schreiben …':
         'writing settings …',
        'Aufnahme bearbeiten …':
         'editing the recording …',
        'fertig':
         'done',
        'einen Moment …':
         'one moment …',
        'Sicherung anlegen …':
         'creating backup …',
        'Liste neu lesen …':
         'reading the list …',
        '  Keine Eingabe möglich — Ende.':
         '  No input possible — end.',
        'Sicherungen löschen':
         'Deleting backups',
        '%d von %d gelöscht':
         '%d of %d deleted',
        '%d von %d gelöscht: %s':
         '%d of %d deleted: %s',
        'Alle normieren':
         'Normalise all',
        '%d von %d Patches: %s':
         '%d of %d patches: %s',
        'Sicherung anlegen':
         'Creating backup',
        '%d von %d Dateien · %.0f MB':
         '%d of %d files · %.0f MB',
        'Doppelklick spielt · Ziehen ordnet um · ⌘ zwei markieren, dann Tauschen · Esc hält an · ⌘O Karte, ⌘S Sicherungen, ⌘L Lücken':
         'Double click plays · drag reorders · ⌘ marks two, then Swap · Esc stops · ⌘O card, ⌘S backups, ⌘L gaps',
        'Setlist-Dateien':
         'Setlist files',
        'Anpassen':
         'Adjust',
        'Erst eine Datei markieren.':
         'Mark a file first.',
        '%d Datei(en) angepasst, %d Zeile(n) umgeschrieben.':
         '%d file(s) adjusted, %d line(s) rewritten.',
        'In diesem Ordner liegen keine Setlist-Dateien:\n%s\n\nBitte einen anderen Ordner wählen.':
         'There are no setlist files in this folder:\n%s\n\nPlease choose another folder.',
        'Alle sind markiert — einzelne mit Klick abwählen.':
         'All are marked — click to unmark single ones.',
        'betrifft Platz: %s':
         'affects slot: %s',
        'Pegeländerung zurücknehmen':
         'Undo level change',
        'Es gibt noch keine Pegelprotokolle.':
         'There are no level logs yet.',
        'Pegelprotokolle':
         'Level logs',
        'Welche Änderung soll zurückgenommen werden?':
         'Which change should be undone?',
        'Erst ein Protokoll markieren.':
         'Mark a log first.',
        'Im Protokoll stehen keine Einträge.':
         'The log contains no entries.',
        'Ein Patch (Platz %s)':
         'One patch (slot %s)',
        '%d Patches':
         '%d patches',
        '%s aus diesem Protokoll werden umgekehrt:\n\n%s\n\nNur diese Patches werden vorher gesichert. Fortfahren?':
         '%s from this log are reversed:\n\n%s\n\nOnly those patches are backed up first. Continue?',
        'Pegeländerung zurücknehmen …':
         'Undo level change …',
        '  Trockenlauf — nichts geändert.':
         '  Dry run — nothing changed.',
        '  Jetziger Stand gesichert: %s':
         '  Current state saved: %s',
        '  Einstellungen von Patch%d zurückgeholt.':
         '  Settings of patch %d restored.',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe kommen aus dieser Sicherung zurück:\n\n  %s\n\nDie Aufnahme bleibt unberührt — sie war in dieser Sicherung nicht enthalten.\nDer jetzige Stand wird vorher gesichert.':
         'Patch %d: name, stop mode, rhythm and playback come back from this backup:\n\n  %s\n\nThe recording is left alone — it was not part of this backup.\nThe current state is saved first.',
        'Patch%d wird aus dieser Sicherung wiederhergestellt:\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen.\nDer jetzige Stand wird vorher gesichert.':
         'Patch %d is restored from this backup:\n\n  %s\n\nThis can undo a fade, for example.\nThe current state is saved first.',
        'Patch%d zurückgeholt':
         'Patch %d restored',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nPatches, die schon passen, bleiben unangetastet. Nur die\nübrigen werden vorher gesichert und neu geschrieben.\n\nFortfahren?':
         'All patches are brought to the same loudness:\n\n  target -18 dB loudness, peak at most -1 dB\n\nPatches that already match are left untouched. Only the others are\nbacked up and rewritten.\n\nContinue?',
        '  Nichts zu tun: alle Patches liegen bereits auf der Ziel-Lautheit.':
         '  Nothing to do: every patch is already at the target loudness.',
        '  Ein Patch war schon auf Ziel und blieb unverändert.':
         '  One patch was already at target and stayed untouched.',
        '  Schon auf Ziel: %d von %d Patches — unverändert gelassen.':
         '  Already at target: %d of %d patches — left untouched.',
        '  %d Patches waren schon auf Ziel und blieben unverändert.':
         '  %d patches were already at target and stayed untouched.',
        'Blättern':
         'Browsing',
        'Normierung zurücknehmen …':
         'Undo normalisation …',
        'auf −1 dB Spitze normalisieren':
         'normalise to −1 dB peak',
        'Anhebung höchstens auf 0 dB':
         'limit boost to 0 dB at most',
        'Sicherungen verwalten …':
         'Manage backups …',
        'Lücken bei den Plätzen %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         'Gaps at slots %s%s — “Close the gaps” moves the following slots forward.',
        'Zurücknehmen (letzter Schritt)':
         'Undo (last step)',
        'Zurücknehmen':
         'Undo',
        'ältere markieren':
         'mark the older ones',
        'Anderer Ordner …':
         'Another folder …',
        'Neue Sicherung anlegen (alles sichern)':
         'Create a new backup (everything)',
        'Sicherungen …':
         'Backups …',
        'Ordner mit den Sicherungen':
         'Folder with the backups',
        'Sammelsicherung':
         'Rescue backup',
        'Rette':
         'Rescuing',
        'Es gibt keine unersetzlichen Patches.':
         'There are no irreplaceable patches.',
        '%d Patches wurden nach %s kopiert. Die Zuordnung steht dort in Rettung.txt. Jetzt lassen sich die alten Sicherungen löschen.':
         '%d patches were copied to %s. The mapping is in Rettung.txt in that folder. The old backups can now be deleted.',
        'Nur die unersetzlichen Patches retten':
         'Rescue only the irreplaceable patches',
        '⟨%d Patch nur hier⟩':
         '⟨%d patch only here⟩',
        '%d markiert · %.1f MB':
         '%d marked · %.1f MB',
        'nichts markiert':
         'nothing marked',
        'Vorgabe: neueste 3 und letzte 7 Tage behalten':
         'Default: keep the newest 3 and the last 7 days',
        'Fertig. Ordner:\n%s':
         'Done. Folder:\n%s',
        'Alte Sicherungen in diesem Ordner ansehen und löschen?':
         'Look at the older backups in this folder and delete some?',
        'Lücke bei Platz %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         'Gap at slot %s%s — “Close the gaps” moves the following slots forward.',
        'Platz %s ist nicht belegt — die Liste hat eine Lücke.':
         'Slot %s is not occupied — there is a gap in the list.',
        'Lücken schließen':
         'Close the gaps',
        'Patch%d → Patch%d':
         'Patch%d → Patch%d',
        '%d Patches rücken nach vorn: %s%s\n\nDie Reihenfolge bleibt, nur die Platznummern ändern sich. Die Zuordnung wird vorher gesichert. Setlist-Dateien, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d patches move forward: %s%s\n\nThe order stays the same, only the slot numbers change. The mapping is saved beforehand. Setlist files that refer to slot numbers will no longer match. Continue?',
        'Aufgerückt: %d Patches':
         'Moved up: %d patches',
        'Sollen vorhandene Setlist-Dateien auf die neuen Platznummern umgeschrieben werden?':
         'Should existing setlist files be rewritten to the new slot numbers?',
        'Ordner mit Setlist-Dateien':
         'Folder with setlist files',
        'Setlists angepaßt: %d Datei(en)':
         'Setlists adjusted: %d file(s)',
        'Fehler: Platz %d ist belegt (%s).':
         'Error: slot %d is occupied (%s).',
        'Beenden':
         'Quit',
        'Dem Python fehlen die Zertifikate. Zwei Wege: im Python-Ordner „Install Certificates.command“ ausführen — oder „pip install certifi“ — oder die Datei von der Release-Seite von Hand laden.':
         'Python is missing its certificates. Two ways: run “Install Certificates.command” in the Python folder, or “pip install certifi” — or download the file from the release page by hand.',
        'Achtung: das Zertifikat ließ sich nicht prüfen — die Datei wurde ohne Prüfung geladen.':
         'Careful: the certificate could not be verified — the file was downloaded unchecked.',
        'Die Datei ist unvollständig: %d statt %d Bytes.':
         'The file is incomplete: %d instead of %d bytes.',
        'Neue Fassung %s wird geholt …':
         'Fetching version %s …',
        'Fassung %s ist installiert. Bitte das Fenster schließen und neu starten.':
         'Version %s is installed. Please close the window and start again.',
        'Update beim Start':
         'Update on start',
        'Die Karte ist eingebunden, aber das Dateisystem liefert\nLesefehler:\n\n%s\n\nDas liegt an der Karte oder am Einhängepunkt, nicht am\nWerkzeug.\n\nErste Schritte:\n  - Karte im JamMan einschalten (das Pedal prüft sie)\n  - dmesg | tail -20\n  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n    bzw. sudo exfatfsck <gerät>':
         'The card is mounted, but the file system reports read errors:\n\n%s\n\nThat is the card or the mount point, not the tool.\n\nFirst steps:\n  - put the card into the JamMan and switch it on (the pedal checks it)\n  - dmesg | tail -20\n  - unmount and check: sudo fsck.vfat -n <device>\n    or sudo exfatfsck <device>',
        'kein Pedal':
         'no pedal',
        'Beschreibung …':
         'Description …',
        'Rückwärts: ohne ffmpeg keine Hörprobe — das Pedal spielt es trotzdem rückwärts.':
         'Reverse: without ffmpeg there is no preview — the pedal still plays it backwards.',
        'Tempo':
         'Tempo',
        'Suchen …':
         'Search …',
        'Text im Namen (leer lassen = alles zeigen):':
         'Text in the name (leave empty = show all):',
        'Erst eine Karte oder eine geholte Kopie öffnen — dann läßt sich suchen.':
         'Open a card or a fetched copy first — then you can search.',
        'Suchen':
         'Search',
        'Suche fehlgeschlagen: %s':
         'Search failed: %s',
        'Suchen:':
         'Search:',
        'Karte:':
         'Card:',
        'Meldungen stehen im Fenster; Fehler zusätzlich in jamman-fehler.log.':
         'Messages appear in the window; errors also in jamman-fehler.log.',
        'Firmware-Dateien jetzt ins Pedal schreiben? Ein Abbruch mittendrin kann das Pedal unbrauchbar machen. [ja/nein] ':
         'Write the firmware files to the pedal now? An interruption can make the pedal unusable. [yes/no] ',
        'Zielordner für die Firmware':
         'Target folder for the firmware',
        'Ordner mit der geholten Firmware':
         'Folder with the fetched firmware',
        'In diesem Ordner liegt keine Firmware (usr/JamManUI, usr/loopengine …).':
         'This folder contains no firmware (usr/JamManUI, usr/loopengine …).',
        'Firmware übernehmen':
         'Apply firmware',
        'Firmware übernehmen …':
         'Apply firmware …',
        'Firmware dieses Pedals holen …':
         "Fetch this pedal's firmware …",
        '%s kam unvollständig (%d von %d Bytes).':
         '%s came incomplete (%d of %d bytes).',
        '%d Firmware-Dateien werden jetzt in das Pedal geschrieben. Vorher wird der jetzige Stand gesichert.\n\nEin Abbruch mittendrin kann das Pedal unbrauchbar machen — es gibt keinen Weg zurück.\n\nWirklich schreiben?':
         '%d firmware files will now be written to the pedal. The current state is backed up first.\n\nAn interruption can make the pedal unusable — there is no way back.\n\nReally write?',
        'Sichere den jetzigen Stand …':
         'Backing up the current state …',
        'Gesichert: %d Dateien nach %s':
         'Backed up: %d files to %s',
        'Firmware geholt: %d Dateien':
         'Firmware fetched: %d files',
        'Firmware geholt:\n%s\n%d Dateien':
         'Firmware fetched:\n%s\n%d files',
        'Fertig: %d Dateien geschrieben':
         'Done: %d files written',
        'Nicht bestätigt: %d':
         'Not confirmed: %d',
        'Firmware geschrieben: %d Dateien\nSicherung: %s\nJetzt das Pedal aus- und wieder einschalten.':
         'Firmware written: %d files\nBackup: %s\nNow switch the pedal off and on again.',
        'Schreibe':
         'Writing',
        'Hole':
         'Fetching',
        'JamList aus JamManager XT holen …':
         'Fetch JamList from JamManager XT …',
        'Setlist als JamList ablegen …':
         'Store setlist as JamList …',
        'Setlist speichern':
         'Save setlist',
        'Bibliothek von JamManager XT':
         'JamManager XT library',
        'JamList wählen':
         'Choose a JamList',
        'In dieser Bibliothek liegt keine JamList.':
         'This library contains no JamList.',
        'Geholt: %s\n%d Plätze\n%s':
         'Fetched: %s\n%d slots\n%s',
        'JamList ablegen':
         'Store JamList',
        '%s%s%s als neue JamList eintragen? Die Bibliothek wird dabei verändert — vorher eine Kopie anlegen.':
         'Add %s%s%s as a new JamList? The library will be changed — make a copy first.',
        'JamList %s angelegt (id %d, %d Plätze)':
         'JamList %s created (id %d, %d slots)',
        'Keine JamList mit dem Namen %s.':
         'No JamList named %s.',
        'belegte Plätze':
         'occupied slots',
        'Geschrieben: %s (%d Plätze)':
         'Written: %s (%d slots)',
        'Übernehmen':
         'Apply',
        'Speicherabbild':
         'Memory image',
        'Speicherabbild speichern':
         'Save memory image',
        'Speicherabbild aufnehmen …':
         'Take a memory image …',
        'Speicherabbilder vergleichen …':
         'Compare memory images …',
        'Speicherabbilder vergleichen':
         'Compare memory images',
        'Erstes Speicherabbild':
         'First memory image',
        'Zweites Speicherabbild':
         'Second memory image',
        'Nur im ersten Bild: %d':
         'Only in the first image: %d',
        'Nur im zweiten Bild: %d':
         'Only in the second image: %d',
        'Gleicher Pfad, andere Größe: %d':
         'Same path, different size: %d',
        'Die beiden Bilder sind gleich.':
         'The two images are identical.',
        'Erstes:':
         'First:',
        'Zweites:':
         'Second:',
        'Aufruf:':
         'Usage:',
        'Lese den Verzeichnisbaum …':
         'Reading the directory tree …',
        'Speicherabbild: %d Einträge':
         'Memory image: %d entries',
        'Speicherabbild geschrieben:\n%s':
         'Memory image written:\n%s',
        'Speicherabbild geschrieben: %d Einträge nach %s':
         'Memory image written: %d entries to %s',
        'Dateien für „speicherabbild“ und „vergleich“':
         'Files for “speicherabbild” and “vergleich”',
        'Sortierung':
         'Sorting',
        '%d von %d Patches passen zu „%s“.':
         '%d of %d patches match “%s”.',
        'Beschreibung':
         'Description',
        'Interpret':
         'Artist',
        'Genre':
         'Genre',
        'Stichwörter':
         'Tags',
        'Bewertung':
         'Rating',
        'Tempo (BPM)':
         'Tempo (BPM)',
        'Die Datei %s fehlt.':
         'The file %s is missing.',
        'Setlists anpassen':
         'Adjust setlists',
        '%d Setlist-Dateien in %s an die neuen Plätze anpassen? Jede Datei wird vorher daneben gesichert.':
         'Adjust %d setlist files in %s to the new slots? Each file is backed up next to it first.',
        'Keine Setlist nannte einen bewegten Platz.':
         'No setlist mentioned a moved slot.',
        'Setlists nicht angepaßt: ':
         'Setlists not adjusted: ',
        'Setlists angepaßt: ':
         'Setlists adjusted: ',
        'Plätze aufrücken':
         'Close the gaps',
        'Plätze aufrücken …':
         'Close the gaps …',
        'Es gibt keine Lücken — die Plätze sind schon lückenlos belegt.':
         'There are no gaps — the slots are already contiguous.',
        '%d Patches rücken in %s nach vorn: %s%s\n\nJeder bewegte Platz wird vorher auf den Rechner gesichert. Setlists im Pedal, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d patches move up in %s: %s%s\n\nEvery moved slot is backed up to the computer first. Setlists in the pedal that point at slot numbers will no longer match. Continue?',
        'Rücke auf':
         'Moving up',
        'Fertig: %d Plätze aufgerückt, %d vorher gesichert':
         'Done: %d slots moved up, %d backed up first',
        'Aufgerückt: %d Plätze':
         'Moved up: %d slots',
        'Kopie auf dem Rechner — Änderungen hier wirken nicht auf das Pedal.':
         'Copy on the computer — changes here do not affect the pedal.',
        'Patch%d wird aus der Kopie auf dem Rechner entfernt. Das Pedal bleibt unverändert. Fortfahren?':
         'Patch%d will be removed from the copy on the computer. The pedal stays unchanged. Continue?',
        'Patch%d wird von der Karte entfernt. Vorher wird er auf dem Rechner gesichert. Fortfahren?':
         'Patch%d will be removed from the card. It is backed up to the computer first. Continue?',
        'Ja':
         'Yes',
        'Nein':
         'No',
        'Dem Python fehlen die Zertifikate. Auf macOS hilft „Install Certificates.command“ im Python-Ordner, oder: pip install certifi':
         'Python has no certificates. On macOS run “Install Certificates.command” in the Python folder, or: pip install certifi',
        'GitHub ist nicht erreichbar: %s':
         'GitHub cannot be reached: %s',
        'unbekannter Grund':
         'unknown reason',
        'Gelöscht: %s':
         'Deleted: %s',
        'Fehler:':
         'Error:',
        'Platz löschen':
         'Delete slot',
        'Welcher Platz soll gelöscht werden? (z. B. 30 oder Patch30)':
         'Which slot should be deleted? (e.g. 30 or Patch30)',
        'Platz löschen …':
         'Delete slot …',
        '%s ist nicht belegt — nichts zu löschen.':
         '%s is empty — nothing to delete.',
        '%s wird aus %s gelöscht. Das läßt sich nicht rückgängig machen. Fortfahren?':
         '%s will be deleted from %s. This cannot be undone. Continue?',
        'Lösche':
         'Deleting',
        'Gelöscht:':
         'Deleted:',
        'Fehler beim Löschen:':
         'Error while deleting:',
        'Die Datei ist %s lang — das Pedal nimmt höchstens 10 Minuten je Loop an. Bitte im Hauptfenster kürzen oder teilen.':
         'The file is %s long — the pedal accepts at most 10 minutes per loop. Please shorten or split it in the main window.',
        'Dieser Ordner ist nicht lesbar: %s':
         'This folder cannot be read: %s',
        'Auswahl:':
         'Selection:',
        'Pegelprotokoll wählen':
         'Choose level log',
        'Audiodatei zum Teilen auswählen':
         'Choose the audio file to split',
        'Dateiname:':
         'File name:',
        'Speichern':
         'Save',
        'Bitte einen Dateinamen angeben.':
         'Please enter a file name.',
        'Überschreiben':
         'Overwrite',
        '%s gibt es schon. Ersetzen?':
         '%s already exists. Replace?',
        'Speicherkarte des JamMan wählen':
         'Choose the JamMan card',
        'Zielordner für die Sicherung':
         'Target folder for the backup',
        'Dateien':
         'Files',
        'Nach oben':
         'Up',
        'Öffnen':
         'Open',
        'Bitte eine Datei auswählen.':
         'Please choose a file.',
        'Alle Dateien':
         'All files',
        'Die Datei konnte nicht geschrieben werden: %s':
         'The file could not be written: %s',
        'ohne':
         'none',
        'ende':
         'end',
        'klick':
         'click',
        '%d Patches ohne Ton — nur die Beschreibung ist geholt.':
         '%d patches without audio — only the description has been fetched.',
        'Ganzen Ordner aufs Pedal …':
         'Whole folder to the pedal …',
        'Ordner mit Patch-Ordnern wählen':
         'Choose a folder containing patch folders',
        'Ganzen Ordner aufs Pedal':
         'Whole folder to the pedal',
        '%d Patch-Ordner werden in %s geschrieben. Belegte Plätze werden vorher gesichert, unveränderte übersprungen. Fortfahren?':
         '%d patch folders will be written to %s. Occupied slots are backed up first, unchanged ones are skipped. Continue?',
        'internen Speicher':
         'internal memory',
        'der Karte':
         'the card',
        'Im Ordner liegen keine Patch-Ordner (erwartet: Patch01 … Patch200).':
         'The folder contains no patch folders (expected: Patch01 … Patch200).',
        'Fertig: %d Patches geschrieben, %d übersprungen, %d vorher gesichert':
         'Done: %d patches written, %d skipped, %d backed up first',
        'Fehler beim Schreiben:':
         'Error while writing:',
        'Übertragen: %d Dateien · unverändert übersprungen: %d':
         'Transferred: %d files · skipped unchanged: %d',
        'Zielplatz auf dem Pedal (z. B. 17 oder Patch17):':
         'Target slot on the pedal (e.g. 17 or Patch17):',
        'Unerwarteter Fehler':
         'Unexpected error',
        'Das Werkzeug wurde unerwartet beendet.':
         'The tool stopped unexpectedly.',
        'Protokoll:':
         'Log:',
        'Auswahl kopieren':
         'Copy selection',
        'Nichts markiert — bitte zuerst Text markieren.':
         'Nothing selected — please select some text first.',
        'Anleitung speichern':
         'Save the instructions',
        'Gespeichert.':
         'Saved.',
        'Alles kopieren':
         'Copy all',
        'Als Datei speichern':
         'Save as file',
        'Anleitung':
         'Instructions',
        'Zum Umwandeln fehlt ffmpeg.':
         'ffmpeg is missing for the conversion.',
        'Die Programmdateien lassen sich hier nicht ersetzen: %s':
         'The program files cannot be replaced here: %s',
        'Unerwarteter Fehler: %s':
         'Unexpected error: %s',
        'Aktualisieren':
         'Update',
        'Die neueste Fassung wird von GitHub geladen und ersetzt die Programmdateien. Ihre Einstellungen und Ihre Karte bleiben unberührt. Fortfahren?':
         'The latest version is downloaded from GitHub and replaces the program files. Your settings and your card stay untouched. Continue?',
        'Jetzt aktualisieren?':
         'Update now?',
        'Aktualisiert':
         'Updated',
        'Nicht aktualisiert':
         'Not updated',
        'Fenster jetzt schließen?':
         'Close the window now?',
        'GitHub ist nicht erreichbar.':
         'GitHub cannot be reached.',
        'Sie benutzen bereits die neueste Fassung (%s).':
         'You are already using the latest version (%s).',
        'Zur Fassung %s gibt es keine Datei zum Herunterladen.':
         'Version %s has no file to download.',
        'Lade Fassung %s …':
         'Downloading version %s …',
        'Herunterladen fehlgeschlagen: %s':
         'Download failed: %s',
        'Die heruntergeladene Datei ist kein gültiges ZIP.':
         'The downloaded file is not a valid ZIP.',
        'Die Datei ist beschädigt (%s).':
         'The file is damaged (%s).',
        'In der Datei fehlt jamman.py.':
         'The file does not contain jamman.py.',
        'Die Datei gehört nicht zur Fassung %s.':
         'The file does not belong to version %s.',
        'Fassung %s ist installiert (%d Dateien). Bitte das Fenster schließen und neu starten.':
         'Version %s is installed (%d files). Please close the window and start again.',
        'Die Karte hängt nicht im Rechner — nichts auszuwerfen. Steckt sie im Pedal, führt das Pedal sie nicht als Laufwerk.':
         'The card is not in the computer — nothing to eject. If it is in the pedal, the pedal does not offer it as a drive.',
        'Auswerfen ging nicht. Bitte im Dateimanager auf „Auswerfen" klicken.':
         'Ejecting failed. Please use “Eject” in the file manager.',
        'Keine Karte im Rechner gefunden.':
         'No card found in the computer.',
        'Karte im Rechner:  „Karte wählen“ drücken.':
         'Card in the computer:  press “Choose card”.',
        'Karte im Pedal:    oben rechts „Pedal über USB“ — dort sind Karte und interner Speicher erreichbar.':
         'Card in the pedal:  top right “Pedal over USB” — the card and the internal memory are reachable there.',
        'Speicherkarte': 'Memory card',
        'Kopie vom Pedal': 'copy from the pedal',
        'Speicherkarte des JamMan Stereo': 'Memory card of the JamMan Stereo',
        'Im Pedal steckt keine Karte.': 'There is no card in the pedal.',
        'Karte einlegen und dann „Karte wählen“ oder „Neu laden“ drücken.':
         'Insert the card, then press “Choose card” or “Reload”.',
        'keine Karte':
         'no card',
        'Dies ist eine Kopie vom Pedal, nicht die Karte selbst. Über „Karte wählen“ die Karte öffnen.':
         'This is a copy taken from the pedal, not the card itself. Use “Choose card” to open the card.',
        'JamMan Card Tool': 'JamMan Card Tool',
        'Karte und interner Speicher': 'card and internal memory',
        '(für Rückgängig nötig)':
         '(needed for Undo)',
        'Darunter ist die Sicherung für Rückgängig. Danach läßt sich der letzte Schreibvorgang nicht mehr zurücknehmen. Trotzdem löschen?':
         'This includes the backup for Undo. The last write can then no longer be undone. Delete anyway?',
        'Sicherungen aufräumen …':
         'Clean up backups …',
        'Sicherungen aufräumen':
         'Clean up backups',
        'Sicherungen im Zielordner':
         'Backups in the target folder',
        '%d Sicherungen · %.1f MB':
         '%d backups · %.1f MB',
        'Neueste behalten:':
         'Keep newest:',
        'Markierte löschen':
         'Delete selected',
        'Erst Sicherungen in der Liste markieren.':
         'Select backups in the list first.',
        '%d Sicherungen mit %.1f MB werden endgültig gelöscht. Fortsetzen?':
         '%d backups totalling %.1f MB will be deleted permanently. Continue?',
        '%d Sicherungen konnten nicht gelöscht werden.':
         '%d backups could not be deleted.',
        'Der Zielordner ist noch nicht angelegt.':
         'The target folder does not exist yet.',
        'Im Zielordner liegen keine Sicherungen.':
         'There are no backups in the target folder.',
        'Alle auswählen':
         'Select all',
        'Nichts markiert. Alle %d Patches in die Setlist?':
         'Nothing selected. Put all %d patches into the setlist?',
        'Warte auf das Ende des laufenden Vorgangs …':
         'Waiting for the running operation to finish …',
        'Der Vorgang reagiert nicht — Fenster schließen und neu öffnen.':
         'The operation is not responding — close the window and open it again.',
        'Hole Beschreibungen': 'Fetching descriptions',
        'Es wurde kein Zielplatz eingegeben.':
         'No target slot was entered.',
        'Der Zielplatz muß wie Patch01 … Patch200 heißen, nicht „%s“.':
         'The target slot has to be named like Patch01 … Patch200, not “%s”.',
        'OK': 'OK',
        'Ein Vorgang läuft noch — bitte abwarten.':
         'An operation is still running — please wait.',
        'Verbindung neu aufbauen':
         'Reconnect',
        'Verbindung wird neu aufgebaut …':
         'Reconnecting …',
        'Lesen und Schreiben über USB. Vor jedem Überschreiben wird der alte Platz auf den Rechner gesichert.':
         'Reading and writing over USB. Before anything is overwritten, the old slot is saved to the computer.',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller. Schreiben verändert die Karte — vorher wird gesichert.':
         'Data flows over USB at about 1.3 MB/s. For a whole card a card reader is much faster. Writing changes the card — it is saved first.',
        'Audio in den Speicher schreiben …': 'Write audio into the memory …',
        'Beim JamMan Solo XT behält der FS3X seine Grundfunktionen. Eine eigene Belegung speichert das Pedal zwar, wertet sie aber nicht aus — deshalb wird sie hier nicht angeboten.':
         'On the JamMan Solo XT the FS3X keeps its basic functions. The pedal does store a custom assignment but does not act on it, so it is not offered here.',
        'Änderungen wirken nach einem Neustart des Pedals.': 'Changes take effect after restarting the pedal.',
        'gewünscht': 'wanted',
        'Pedal meldet': 'pedal reports',
        '(nicht belegt)':
         '(not assigned)',
        'UndoRedo = Rückgängig/Wiederholen · Reverse = Rückwärts · Tempo = Tempo antippen':
         'UndoRedo = undo/redo · Reverse = backwards · Tempo = tap tempo',
        'Sichern': 'Save',
        'Schreiben': 'Write',
        'Setlists': 'Setlists',
        'Pedal': 'Pedal',
        'Ins Hauptfenster holen …': 'Fetch into the main window …',
        'Einzelnen Patch sichern …': 'Save a single patch …',
        'Patch-Ordner aufs Pedal …': 'Patch folder to the pedal …',
        'Übernommen.': 'Applied.',
        'Pedal spielt jetzt:': 'The pedal now plays:',
        'Audio aufs Pedal …':
         'Audio to the pedal …',
        'Audio aufs Pedal':
         'Audio to the pedal',
        'Audiodatei wählen':
         'Choose an audio file',
        'Audio':
         'Audio',
        'Sichere den alten Platz …':
         'Saving the old slot …',
        'Wandle die Datei …':
         'Converting the file …',
        'Umwandeln fehlgeschlagen — die Datei muß 44,1 kHz, 16 Bit, Stereo werden.':
         'Conversion failed — the file has to become 44.1 kHz, 16 bit, stereo.',
        'Leere den Platz …': 'Clearing the slot …',
        'Pedal spielt jetzt Bank/Patch:': 'The pedal now plays bank/patch:',
        'Karte erkannt:': 'Card detected:',
        'Als ZIP sichern …':
         'Save as ZIP …',
        'Als ZIP sichern':
         'Save as ZIP',
        'Sichere als ZIP …':
         'Saving as ZIP …',
        'Sichere:':
         'Saving:',
        'Fehler beim Sichern:':
         'Error while saving:',
        'Die Setlist ist leer.':
         'The setlist is empty.',
        'Erst Patches in der Liste markieren (⌘-Klick).':
         'Select patches in the list first (⌘-click).',
        'Es gibt nichts rückgängig zu machen.':
         'There is nothing to undo.',
        'Fußschalter …':
         'Footswitch …',
        'Fußschalter':
         'Footswitch',
        'Tastenbelegung des Pedals':
         'Footswitch assignment of the pedal',
        'Werte sind Geräteeinstellungen — nur ändern, wenn du weißt, was sie bedeuten.':
         'These are device settings — only change them if you know what they mean.',
        'JamMan':
         'JamMan',
        'Karte':
         'card',
        'intern':
         'internal',
        'Liste als Textdatei sichern':
         'Save list as a text file',
        'Liste als Textdatei …':
         'List as a text file …',
        'Mitlaufen':
         'Follow',
        'Mitlaufen an — am Pedal einen Patch wählen.':
         'Follow on — select a patch on the pedal.',
        'Mitlaufen aus.':
         'Follow off.',
        'Rückgängig':
         'Undo',
        'Setlist holen …':
         'Fetch setlist …',
        'Setlist laden':
         'Load setlist',
        'Setlist sichern …':
         'Save setlist …',
        'Setlist sichern':
         'Save setlist',
        'Setlist.txt':
         'Setlist.txt',
        'Stelle wieder her:':
         'Restoring:',
        '%s wird durch die Sicherung ersetzt. Fortsetzen?':
         '%s will be replaced by the backup. Continue?',
        'Ordner des Patches wählen':
         'Choose the patch folder',
        'Aufs Pedal schreiben':
         'Write to the pedal',
        '%s ist schon belegt. Der Patch wird vorher auf den Rechner gesichert und dann überschrieben. Fortsetzen?':
         '%s is already in use. That patch will be saved to the computer first and then overwritten. Continue?',
        'Schreibe aufs Pedal …':
         'Writing to the pedal …',
        'Schreibe:':
         'Writing:',
        'Patch aufs Pedal schreiben …':
         'Write patch to the pedal …',
        '(Vorschlag)': '(suggested)',
        'Ziel:': 'Target:',
        'noch nicht gewählt': 'not chosen yet',
        'Ziel ändern …': 'Change target …',
        'Aufräumen':
         'Clean up',
        'Die angefangene Kopie entfernen? Nur die gerade geholten Dateien werden gelöscht, der Ordner bleibt.':
         'Remove the unfinished copy? Only the files fetched just now are deleted; the folder stays.',
        'Aufgeräumt:':
         'Cleaned up:',
        'Hole Beschreibungen …': 'Fetching descriptions …',
        'Wird abgebrochen …': 'Cancelling …',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller — steckt die Karte im Pedal, bleibt nur dieser Weg.':
         'Data flows over USB at about 1.3 MB/s. For a whole card a card reader is much faster — if the card is in the pedal, this is the only way.',
        'Abbrechen': 'Cancel',
        'Abgebrochen.': 'Cancelled.',
        'Fehler beim Öffnen:': 'Error while opening:',
        'Alle ins Hauptfenster holen …': 'Fetch all into the main window …',
        'Zielordner wählen': 'Choose a target folder',
        'Hole Patch': 'Fetching patch',
        'Fertig:': 'Done:',
        'Im Hauptfenster öffnen?': 'Open in the main window?',
        'Gesichert': 'Saved',
        'Pedal über USB': 'Pedal over USB',
        'Das USB-Modul fehlt: greenglass.py muß neben jamman.py liegen.': 'The USB module is missing: greenglass.py must sit next to jamman.py.',
        'Gesichert nach:': 'Saved to:',
        'Interner Speicher': 'Internal memory',
        'SD-Karte': 'SD card',
        'Kein JamMan gefunden — Pedal einschalten und Kabel prüfen.': 'No JamMan found — switch the pedal on and check the cable.',
        '(ohne Namen)': '(no name)',
        'Fehler beim Lesen:': 'Error while reading:',
        'Erst „Neu laden“ drücken.': 'Press "Reload" first.',
        'Lädt …': 'Loading …',
        'Nur lesen: das Pedal wird nicht verändert.': 'Read-only: the pedal is not modified.',
        'Ordner zum Sichern wählen': 'Choose a folder to save to',
        'Patch sichern …': 'Save patch …',
        'Verbinde mit dem Pedal …': 'Connecting to the pedal …',
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
        "Einstellungen: %s": "Settings:      %s",
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
        "  %-28s  (gibt es nicht)": "  %-28s  (does not exist)",
        "  %-28s  (nicht lesbar: %s)": "  %-28s  (not readable: %s)",
        "  %-28s  (leer)": "  %-28s  (empty)",
        "  %-28s  %s mit %s Patches": "  %-28s  %s with %s patches",
        "kein nutzbares Tk": "no usable Tk",
        "WAV-Format": "WAV format",
        "keine WAV": "no WAV",
        "%d Hz / %d Bit / %d K": "%d Hz / %d bit / %d ch",
        "  Hinweis: Pegelprotokoll konnte nicht geschrieben werden.": "    Note: the level protocol could not be written.",
        "  Protokoll: %s": "    Protocol: %s",
        "  %d Einträge": "    %d entries",
        "    %-10s %-24s Faktor %.4f → 1/%.4f": "      %-10s %-24s factor %.4f → 1/%.4f",
        "  Trockenlauf — nichts geändert. Mit --schreiben ausführen.": "    Dry run — nothing changed. Run with --schreiben to write.",
        "    %-10s %+6.1f → %+6.1f dB": "      %-10s %+6.1f → %+6.1f dB",
        "  Zurückgerechnet: %d Patches. Sicherung davor: %s": "    Recalculated: %d patches. Backup before: %s",
        "  Pegel:        %.1f dB → %.1f dB Spitze": "    Level:        %.1f dB → %.1f dB peak",
        "                Die Anhebung wurde begrenzt, damit nichts": "                The boost was limited so that nothing",
        "                über 0 dBFS geht (sonst würde übersteuert).": "                goes above 0 dBFS (that would clip).",
        "                Achtung: %d Werte liegen an der Grenze.": "                Note: %d values sit at the limit.",
        "  Hinweis: unbekanntes WAV-Format 0x%04X - nicht angefasst.": "    Note: unknown WAV format 0x%04X — left untouched.",
        "Fehler: '%s' ist kein Ordner. Ist die Karte eingelegt?": "  Error: '%s' is not a folder. Is the card inserted?",
        "Fehler: auf '%s' liegt kein Ordner '%s' oder '%s'. Das ist keine JamMan-Karte.": "  Error: '%s' contains no folder '%s' or '%s'. That is not a JamMan card.",
        "  %d Apple-Dateien entfernt (._* und .DS_Store).": "    %d Apple files removed (._* and .DS_Store).",
        "  Keine Patches auf der Karte.": "    No patches on the card.",
        "  %-10s %-34s %8s  %s": "    %-10s %-34s %8s  %s",
        "  %-10s %-34s %8s  %s%s%s": "    %-10s %-34s %8s  %s%s%s",
        "\n  %d Patches, %s belegt.": "\n  %d patches, %s used.",
        "  Sicherung beim Start: Karte unverändert, nichts zu tun.": "    Backup at start: card unchanged, nothing to do.",
        "  Sicherung beim Start: %s": "    Backup at start: %s",
        "    gesichert: %d Patches, %.0f MB": "      backed up: %d patches, %.0f MB",
        "    alte Sicherung entfernt: %s": "      old backup removed: %s",
        "  Quelle: %s": "    Source: %s",
        "  Ziel:   %s": "    Target: %s",
        "  Trockenlauf — nichts kopiert. Mit --schreiben ausführen.": "    Dry run — nothing copied. Run with --schreiben to write.",
        "  Gesichert: %d Patches, %.0f MB.": "    Backed up: %d patches, %.0f MB.",
        "  Datei:  %s": "    File:   %s",
        "  Dauer:  %s": "    Length: %s",
        "  Teile:  %d × etwa %s": "    Parts:  %d × about %s",
        "  Suche musikalisch brauchbare Schnittstellen …": "    Looking for musically usable cut points …",
        "  Hinweis: keine brauchbaren Pausen gefunden (%s) -": "    Note: no usable pauses found (%s) —",
        "           es wird gleichmäßig geschnitten.": "             cutting evenly instead.",
        "  Pause an der Naht wird herausgenommen": "    Pause at the join is removed",
        "    Teil %d: %7.1f – %7.1f s  (%s)": "      Part %d: %7.1f – %7.1f s  (%s)",
        "  Hinweis: Teil %d wäre %.1f min - er wird auf 9:59 gekürzt.": "    Note: part %d would be %.1f min — it is shortened to 9:59.",
        "    Teil %d: %s – %s": "      Part %d: %s – %s",
        "  Trockenlauf — nichts angelegt. Mit --schreiben ausführen.": "    Dry run — nothing created. Run with --schreiben to write.",
        "  Quelle:       %s": "    Source:       %s",
        "  Dauer:        %s": "    Length:       %s",
        "  Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s": "    Format:       %d Hz / %d bit / %d channel(s)%s",
        "  Stopp:        %s": "    Stop:         %s",
        "  Rhythmus:     %s": "    Rhythm:       %s",
        "  Wiedergabe:   %s": "    Playback:     %s",
        "  Fade:         letzte %.1f s werden ausgeblendet": "    Fade:         last %.1f s are faded out",
        "  Pegel:        auf -1 dB Spitze normieren": "    Level:        normalise to -1 dB peak",
        "  Pegel:        %+.1f dB": "    Level:        %+.1f dB",
        "  Neuer Patch:  Patch%s  (Name: %s)": "    New patch:    Patch%s  (name: %s)",
        "  Vorlage:      %s": "    Template:     %s",
        "  Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.": "    Dry run — nothing written. Run with --schreiben to write.",
        "  Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)": "    Shorten:      cut to %s (hard cut, 12 ms faded)",
        "  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben": "    Header:       WAV header rewritten to plain PCM",
        "  Fertig: %s angelegt.": "    Done: %s created.",
        "  Karte auswerfen, in den JamMan stecken — der Patch erscheint als Patch%s.": "    Eject the card, put it into the JamMan — the patch appears as Patch%s.",
        "  Patch:        %s  (%s)": "    Patch:        %s  (%s)",
        "  Stopp:        %s (%s)": "    Stop:         %s (%s)",
        "  Rhythmus:     %s (%s)": "    Rhythm:       %s (%s)",
        "  Wiedergabe:   %s (IsLoop %s)": "    Playback:     %s (IsLoop %s)",
        "  Name:         %s": "    Name:         %s",
        "  Fade:         letzte %.1f s der Aufnahme ausblenden": "    Fade:         fade out the last %.1f s of the recording",
        "                (verändert die Audiodatei; vorher wird gesichert)": "                (changes the audio file; a backup is made first)",
        "  Geändert. Sicherung: %s": "    Changed. Backup: %s",
        "  %s Sie kann jetzt abgezogen werden.": "    %s It can be removed now.",
        "  Schreibweise des Geräts: Patch%0*d": "    Device's spelling: Patch%0*d",
        "  Alle Ordner heißen schon so. Nichts zu tun.": "    All folders already look like this. Nothing to do.",
        "  Zu ändern: %d": "    To change: %d",
        "    %-12s ->  %s": "      %-12s ->  %s",
        "    %-12s FEHLER: %s": "      %-12s ERROR: %s",
        "  Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.": "    Done: %d folders renamed, %d errors, %d patches readable.",
        "  Zuordnung liegt in: %s": "    Mapping is in: %s",
        "  %d Apple-Dateien auf der Karte. Mit --schreiben entfernen.": "    %d Apple files on the card. Remove them with --schreiben.",
        "  Fertig.": "    Done.",
        "  Spielt \"%s\" (%s) … (Strg+C hält an)": "    Playing \"%s\" (%s) … (Ctrl+C stops)",
        "  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB": "    Target: %.1f dB loudness (RMS), peak at most %.1f dB",
        "  Patches: %d": "    Patches: %d",
        "  %-10s %-24s %8s": "    %-10s %-24s %8s",
        "  %-10s %-24s %+7.1f dB": "    %-10s %-24s %+7.1f dB",
        "  Sicherung: %s": "    Backup: %s",
        "  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s": "    %-10s %-24s %+6.1f → %+6.1f dB  (peak %+.1f dB)%s",
        "  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.": "    Done: %d patches normalised, %d of them capped at the peak.",
        "  Patches in der Sicherung: %d": "    Patches in the backup: %d",
        "  Patches auf der Karte:    %d": "    Patches on the card:    %d",
        "  Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:": "    Note: these do not exist on the card and will be created:",
        "           %s": "             %s",
        "  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.": "    Dry run — nothing restored. Run with --schreiben to write.",
        "  Zurückgeholt. Der Zustand davor liegt in: %s": "    Restored. The previous state is in: %s",
        "  Patch:      %s  (%s)": "    Patch:      %s  (%s)",
        "  Sicherung:  %s": "    Backup:     %s",
        "    %-22s %8.2f MB": "      %-22s %8.2f MB",
        "  %s  (%s)": "    %s  (%s)",
        "  →  %s": "    →  %s",
        "  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.": "    Dry run — nothing renamed. Run with --schreiben to write.",
        "  Fertig. Sicherung: %s": "    Done. Backup: %s",
        "  Nichts zu tun.": "    Nothing to do.",
        "  Achtung: %s und %s haben unterschiedliche Stellenzahl.": "    Note: %s and %s have a different number of digits.",
        "           Der JamMan sortiert nach Namen - der Patch landet dann": "             The JamMan sorts by name — the patch will land",
        "           auf dem Platz, den sein neuer Name angibt.": "             on the slot its new name says.",
        "  Tauschen:": "    Swapping:",
        "    Platz %-4s %-24s  →  Platz %s": "      slot %-4s %-24s  →  slot %s",
        "  Trockenlauf — nichts getauscht. Mit --schreiben ausführen.": "    Dry run — nothing swapped. Run with --schreiben to write.",
        "  Getauscht. Sicherung: %s": "    Swapped. Backup: %s",
        "  Wird gelöscht: %s  (%s)": "    Will be deleted: %s  (%s)",
        "  Vorher wird er gesichert nach: %s": "    It is backed up first to: %s",
        "  Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.": "    Dry run — nothing deleted. Run with --schreiben to write.",
        "  Gelöscht. Sicherung: %s": "    Deleted. Backup: %s",
        "  Karte auswerfen, in den JamMan stecken — der Patch ist weg.": "    Eject the card, put it into the JamMan — the patch is gone.",
        "  Neue Reihenfolge:": "    New order:",
        "    Platz %d:  %-10s  %s%s": "      slot %d:  %-10s  %s%s",
        "  Verschoben. Sicherung: %s": "    Moved. Backup: %s",
        "  JamMan Solo XT — Speicherkarte verwalten": "    JamMan Solo XT — manage the memory card",
        "  Keine Karte gefunden unter %s.": "    No card found under %s.",
        "  Karte: %s": "    Card: %s",
        "  1  Patches auflisten": "    1  List patches",
        "  2  Patch anhören": "    2  Listen to a patch",
        "  3  Datei aufspielen": "    3  Import a file",
        "  4  Patch ändern (Stopp, Wiedergabe, Name)": "    4  Change a patch (stop, playback, name)",
        "  5  Patch verschieben": "    5  Move a patch",
        "  6  Patch löschen": "    6  Delete a patch",
        "  7  Alles sichern": "    7  Back up everything",
        "  8  Karte auswerfen": "    8  Eject the card",
        "  0  Beenden": "    0  Quit",
        "  %s": "    %s",
        "  System:        %s %s": "    System:        %s %s",
        "  Python:        %s": "    Python:        %s",
        "  Fassung:       %s": "    Version:       %s",
        "  Tk:            %s": "    Tk:            %s",
        "  Tk-Modul:      %s": "    Tk module:     %s",
        "  Tk:            NICHT verfügbar — %s": "    Tk:            NOT available — %s",
        "                 (Fenster geht mit diesem Python nicht;": "                    (the window will not work with this Python;",
        "                  das Textmenü geht trotzdem: jamman.py menu)": "                     the text menu still does: jamman.py menu)",
        "  Ton:           winsound (Windows-eigen)": "    Sound:         winsound (built into Windows)",
        "  Ton:           afplay": "    Sound:         afplay",
        "  Ton:           %s": "    Sound:         %s",
        "  Kartenwurzeln: %s": "    Card roots:    %s",
        "  Einstellungen: %s": "    Settings:      %s",
        "  ffmpeg:        %s": "    ffmpeg:        %s",
        "  Schrift:       %s": "    Font:          %s",
        "  Pythons auf diesem Rechner:": "    Pythons on this machine:",
        "    %-62s %s": "      %-62s %s",
        "  Geprüfte Orte:": "    Places checked:",
        "  Laufwerke mit JamManSoloXT:": "    Drives with JamManSoloXT:",
        "    %-24s %3d Patches  %s": "      %-24s %3d patches  %s",
        "  gewählt:       %s": "    chosen:        %s",
        "  Patches:       %d": "    Patches:       %d",
        "  ACHTUNG: das Dateisystem liefert Lesefehler.": "    WARNING: the file system reports read errors.",
        "  Betroffene Einträge (erste drei): %s": "    Affected entries (first three): %s",
        "  Inhalt von %s:": "    Contents of %s:",
        "    %-24r %s, %s": "      %-24r %s, %s",
        "    nicht lesbar: %s": "      not readable: %s",
        "    %-10s %s": "      %-10s %s",
        "  erste Zeile im Fenster:": "    first line in the window:",
        "    %r": "      %r",
        "JamMan Solo XT": "  JamMan Solo XT",
        "BEFEHL": "COMMAND",
        "  Auswahl: ": "  Choice: ",
        "  Anderer Pfad (leer = beenden): ": "  Another path (empty = quit): ",
        "  Patch-Nummer: ": "  Patch number: ",
        "  Name (leer = Dateiname): ": "  Name (empty = file name): ",
        "  Stopp (fade/instant, leer = wie Vorlage): ": "  Stop (fade/instant, empty = as template): ",
        "  Wiedergabe (loop/single, leer = wie Vorlage): ": "  Playback (loop/single, empty = as template): ",
        "  Pfad zur Audiodatei: ": "  Path to the audio file: ",
        "  Stopp (fade/instant, leer = lassen): ": "  Stop (fade/instant, empty = keep): ",
        "  Wiedergabe (loop/single, leer = lassen): ": "  Playback (loop/single, empty = keep): ",
        "  Neuer Name (leer = lassen): ": "  New name (empty = keep): ",
        "  Wirklich schreiben? (j/n): ": "  Really write? (y/n): ",
        "  Welcher Patch: ": "  Which patch: ",
        "  An welche Stelle: ": "  To which slot: ",
        "  Wirklich verschieben? (j/n): ": "  Really move? (y/n): ",
        "  Zielordner (leer = ~/JamMan-Sicherung): ": "  Target folder (empty = ~/JamMan-Sicherung): ",
        "  Wirklich löschen? (j/n): ": "  Really delete? (y/n): ",
        "Keine Karte gefunden": "No card found",
        "Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay).": "No player found (aplay, paplay, mpv or ffplay).",
        "Fehler: im Protokoll stehen keine Einträge.": "Error: the protocol has no entries.",
        "Fehler: Datei ließ sich nicht umwandeln.": "Error: the file could not be converted.",
        "Fehler: kein vollständiger Patch als Vorlage gefunden.": "Error: no complete patch found to use as a template.",
        "Fehler: nichts angegeben (--stopp, --rhythmus, --wiedergabe, --name oder --fade).": "Error: nothing given (--stopp, --rhythmus, --wiedergabe, --name or --fade).",
        "Fehler: '%s' ist keine lesbare Audiodatei.": "Error: '%s' is not a readable audio file.",
        "Fehler: --teile N oder --laenge SEKUNDEN angeben.": "Error: give --teile N or --laenge SEKUNDEN.",
        "Fehler: %.1f Minuten pro Teil sind zu lang (Grenze %d Minuten).": "Error: %.1f minutes per part is too long (limit %d minutes).",
        "Fehler: '%s' gibt es nicht.": "Error: '%s' does not exist.",
        "Fehler: '%s' ist keine lesbare Audiodatei.%s": "Error: '%s' is not a readable audio file.%s",
        "Fehler: '%s' liegt schon vor.": "Error: '%s' already exists.",
        "Fehler: Die Datei ließ sich nicht kürzen.": "Error: the file could not be shortened.",
        "Fehler: Die Umwandlung hat keine hörbare Aufnahme\n  ergeben (nur Stille). Datei prüfen: %s": "Error: the conversion produced no audible recording\n  (silence only). Check the file: %s",
        "Fehler: Patch%d gibt es nicht.": "Error: patch %d does not exist.",
        "Fehler: Die Aufnahme ließ sich nicht ausblenden (kein 16-Bit-WAV?).": "Error: the recording could not be faded (not a 16-bit WAV?).",
        "Fehler: '%s' ist nicht eingelegt.": "Error: '%s' is not inserted.",
        "Fehler beim Auswerfen: %s": "Error while ejecting: %s",
        "Fehler: Patch%d hat keine WAV.": "Error: patch %d has no WAV.",
        "Fehler: Sicherungsordner '%s' gibt es nicht.": "Error: backup folder '%s' does not exist.",
        "Fehler: in '%s' liegen keine Patch-Ordner.": "Error: '%s' contains no patch folders.",
        "Fehler: keine Sicherung mit einer Aufnahme für Patch%d in '%s'.\n  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade.": "Error: no backup with a recording for patch %d in '%s'.\n  Fade backups are called Sicherung_<date>_vor_fade.",
        "Fehler: Platz %d ist belegt (%s).\n  Zum Tauschen:  tauschen --patch %d --mit %d": "Error: slot %d is taken (%s).\n  To swap:  tauschen --patch %d --mit %d",
        "Fehler: Nummern müssen zwischen 1 und %d liegen.": "Error: numbers must be between 1 and %d.",
        "Fehler: '%s' ist ein Ordner, keine Datei.": "Error: '%s' is a folder, not a file.",
        "Fehler: '%s' ist kein Pegelprotokoll (keine lesbare Textdatei).": "Error: '%s' is not a level protocol (no readable text file).",
        "Fehler: '%s' darf nicht gelesen werden.": "Error: '%s' may not be read.",
        "Fehler: '%s' lässt sich nicht lesen: %s": "Error: '%s' cannot be read: %s",
        "Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n  ffmpeg installieren.": "Error while converting. The file has %d Hz / %d channel(s);\n  44100 Hz and 2 channels are needed. Convert it first or\n  install ffmpeg.",
        "Fehler: '%s' lässt sich nicht schreiben.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Error: '%s' cannot be written.\n  Choose another folder:  --sicherung <path>",
        "Fehler: Patch%d hat keine phrase.wav.": "Error: patch %d has no phrase.wav.",
        "Fehler: Der Pegel ließ sich nicht ändern.": "Error: the level could not be changed.",
        "Fehler: in '%s' darf nicht geschrieben werden.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Error: '%s' may not be written to.\n  Choose another folder:  --sicherung <path>",
        "Fehler: Teil %d ließ sich nicht schneiden.": "Error: part %d could not be cut.",
        "Fehler: Die Datei ist %.1f Minuten lang.\n  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n  nicht erkannt - und können dabei verschwinden.\n\n  So geht es weiter:\n    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n                         12 ms ausgeblendet, damit es nicht knackt)\n    --laenge 480         auf 8:00 kürzen\n    --trotzdem           unverändert schreiben (zum Ausprobieren)": "Error: the file is %.1f minutes long.\n  The JamMan Solo XT takes at most %d minutes per loop\n  (manual, page 60: \"Max. Individual Loop Time: 10 minutes\").\n  Longer loops appear on the card but the pedal does not\n  recognise them - and they can disappear in the process.\n\n  What you can do:\n    --kuerzen            cut to 9:59 (hard cut at the end,\n                         12 ms faded so it does not click)\n    --laenge 480         cut to 8:00\n    --trotzdem           write it unchanged (for trying it out)",
        "Fehler: --wav fehlt.": "Error: --wav missing.",
        "Fehler: --patch fehlt.": "Error: --patch missing.",
        "Fehler: --protokoll fehlt.": "Error: --protokoll missing.",
        "Fehler: --patch fehlt (oder --alle angeben).": "Error: --patch missing (or give --alle).",
        "Fehler: --patch und --auf fehlen.": "Error: --patch and --auf missing.",
        "Fehler: --patch und --mit fehlen.": "Error: --patch and --mit missing.",
        "Fehler: --patch und --nach fehlen.": "Error: --patch and --nach missing.",
        "Neue Fassung verfügbar": "New version available",
        "Es gibt eine neuere Fassung: %s. Sie benutzen %s.":
            "There is a newer version: %s. You are using %s.",
        "Zur Download-Seite wechseln?": "Open the download page?",
        "Es liegt keine JamMan-Karte im Rechner.":
            "There is no JamMan card in this computer.",
        "Nimm die microSD aus dem Pedal und stecke sie in einen Kartenleser "
            "oder Kartenslot. Über das USB-Kabel am Pedal erscheint sie nicht "
            "als Laufwerk.":
            "Take the microSD out of the pedal and put it into a card reader or a "
            "card slot. With the USB cable attached it does not show up as a drive.",
        "Ordner selbst wählen?": "Pick the folder yourself?",
        "Ordner der Karte wählen": "Choose the card's folder",
        "Keine JamMan-Karte": "Not a JamMan card",
        "wie Vorlage": "as template",
        "   ← wird umgewandelt": "   ← will be converted",
        "   ← abweichend": "   ← deviates",
        "   ← über 10 min, Pedal nimmt sie nicht an":
            "   ← over 10 min, the pedal will not accept it",
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
        "Fade am Ende": "Fade at end",
        "Karte wählen": "Choose card",
        "Datei aufspielen": "Import file",
        "Alles sichern": "Back up all",
        "Neu laden": "Reload",
        "Anhören": "Play",
        "Stopp": "Stop",
        "Umbenennen": "Rename",
        "Platz ändern": "Change slot",
        "▲ nach oben": "▲ move up",
        "▼ nach unten": "▼ move down",
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
        "Erst einen Patch in der Liste wählen.": "Select a patch in the list first.",
        "Erst eine Karte wählen.": "Choose a card first.",
        "Bitte eine Platznummer eintragen.": "Please enter a slot number.",
        "Platz %d gibt es auf dieser Karte nicht.": "Slot %d does not exist on this card.",
        "MP3 und M4A brauchen ffmpeg -": "MP3 and M4A need ffmpeg -",
        "WAV geht auch ohne": "WAV works without it",
        "Schließen": "Close",
        "Text kopieren": "Copy text",
        "Karte nicht lesbar": "Card not readable",
        "Fehler": "Error",
        "Hinweis": "Note",
        "Sicherung beim Start: %s": "Backup on start: %s",
    },
    "fr": {
        'DATEIEN':
         'FICHIERS',
        'Funktionstest in allen fünf Sprachen':
         'autotest dans les cinq langues',
        'Dauer und Einstellungen kommen aus Dateigröße und WAV-Kopf — der Ton selbst wird dafür nicht übertragen.':
         "La durée et les réglages n'apparaissent qu'après le transfert — par USB, seul le nom est lu.",
        'Marken außerhalb der Datei wurden verworfen.':
         'Les marques hors du fichier ont été écartées.',
        '%d Teile: %s':
         '%d parties : %s',
        '%d Teile, Schnitt an Pausen gesucht':
         '%d parties, coupes cherchées aux pauses',
        '%d Teile, gleichmäßig geschnitten':
         '%d parties, coupe régulière',
        'in %d Patches geteilt':
         'divisé en %d patches',
        'Marken':
         'Marques',
        'Teilen':
         'Séparer',
        'Alle Marken löschen':
         'Effacer toutes les marques',
        'Alle Pausen als Marken':
         'Toutes les pauses comme marques',
        'Pausen gefunden: %d':
         'Pauses trouvées : %d',
        'Keine Pause gefunden.':
         'Aucune pause trouvée.',
        'Wellenform nicht lesbar.':
         "Forme d'onde illisible.",
        '%s   •   Klick setzt eine Marke, Rechtsklick entfernt sie':
         "%s   •   un clic pose une marque, un clic droit l'enlève",
        'Schwelle %':
         'Seuil %',
        'Pause s':
         'Pause s',
        'Fade ms':
         'Fondu ms',
        'Marken bitte als Zahlen mit Komma, z. B. 60, 120.':
         'Entrez les marques comme nombres séparés par des virgules, p. ex. 60, 120.',
        'Schwelle, Pause und Fade bitte als Zahlen eintragen.':
         'Entrez seuil, pause et fondu comme nombres.',
        'Schwelle bitte als Zahl zwischen 1 und 99.':
         'Le seuil doit être un nombre entre 1 et 99.',
        'Pause bitte als Zahl zwischen 0,01 und 30 Sekunden.':
         'La pause doit être un nombre entre 0,01 et 30 secondes.',
        'Fade bitte als Zahl zwischen 0 und 50.':
         'Le fondu doit être un nombre entre 0 et 50.',
        'Es wird an %d gesetzten Marken geschnitten.':
         'Coupe à %d marques.',
        'Es wird an allen %d gefundenen Pausen geschnitten.':
         'Coupe à toutes les %d pauses trouvées.',
        'Schnittpunkt in Sekunden (mehrfach möglich)':
         'point de coupe en secondes (répétable)',
        'an jeder gefundenen Pause trennen':
         'couper à chaque pause trouvée',
        'Ruhepegel für die Pausenerkennung in Prozent (Vorgabe 35)':
         'niveau de silence pour la détection des pauses (défaut 35)',
        'kürzeste Pause, an der geschnitten wird (Vorgabe 0,25 s)':
         'pause la plus courte où couper (défaut 0,25 s)',
        'Fade an jeder Naht in Millisekunden (Vorgabe 0)':
         'fondu à chaque raccord en millisecondes (défaut 0)',
        'Ende einer Pause':
         "fin d'une pause",
        'Marke':
         'marque',
        'Herausgeben':
         'Exporter',
        'Alles herausgeben …':
         'Tout exporter …',
        'Wohin herausgeben?':
         'Exporter vers où ?',
        'Erst eine Karte öffnen — dann läßt sie sich herausgeben.':
         "Ouvrez d'abord une carte — ensuite elle peut être exportée.",
        'Herausgegeben nach: %s':
         'Exporté vers : %s',
        '%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv und LIESMICH.txt.':
         '%d pistes en WAV, %d emplacements sans son, plus karte.csv et LIESMICH.txt.',
        'Die Karte wurde nur gelesen. Die Dateien brauchen dieses Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text.':
         "La carte a seulement été lue. Les fichiers n'ont pas besoin de cet outil : un WAV reste un WAV, et la liste est du texte.",
        'Herausgabe einer JamMan-Karte':
         "Export d'une carte JamMan",
        'Datei':
         'Fichier',
        'Gerät: %s':
         'Appareil : %s',
        'Aufgenommen: %s':
         'Enregistré : %s',
        'Werkzeug: %s':
         'Outil : %s',
        'In diesem Ordner liegen %d Tonspuren als gewöhnliche WAV-Dateien, benannt nach ihrem Platz und ihrem Namen in der Karte. %d Plätze hatten keinen Ton.':
         "Ce dossier contient %d pistes en fichiers WAV ordinaires, nommées d'après leur emplacement et leur nom sur la carte. %d emplacements n'avaient pas de son.",
        'karte.csv enthält zu jeder Spur: Platz, Name, Datei, Dauer, Stoppart, Wiedergabe, Tempo und Rhythmus. Semikolon als Trennzeichen, damit Excel und LibreOffice sie direkt öffnen.':
         "karte.csv donne pour chaque piste : emplacement, nom, fichier, durée, mode d'arrêt, lecture, tempo et rythme. Séparé par des points-virgules pour qu'Excel et LibreOffice l'ouvrent directement.",
        'Diese Dateien brauchen das Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text. Wer später ein anderes Gerät oder Programm benutzt, kann damit weiterarbeiten. Die Karte selbst wurde nur gelesen, nicht verändert.':
         "Ces fichiers n'ont pas besoin de l'outil : un WAV reste un WAV, et la liste est du texte. Qui passe plus tard à un autre appareil ou logiciel peut continuer avec. La carte elle-même a seulement été lue, jamais modifiée.",
        'Herausgabe: %d WAV-Dateien gezählt, %d erwartet':
         'Export : %d fichiers WAV comptés, %d attendus',
        'Herausgabe: karte.csv fehlt':
         'Export : karte.csv manque',
        'Herausgabe: %d Zeilen in der Liste, %d erwartet':
         'Export : %d lignes dans la liste, %d attendues',
        'Herausgabe: LIESMICH.txt fehlt':
         'Export : LIESMICH.txt manque',
        'Nur-Lesen-Modus: „%s“ ist ein unbekanntes Gerät (%s). Der Kartenaufbau ist nicht belegt, deshalb wird nichts geschrieben. Lesen ist erlaubt (liste). Mit „jamman.py struktur <karte>“ entsteht ein Bericht, den Sie weitergeben können.':
         "Mode lecture seule : « %s » est un appareil inconnu (%s). La structure de la carte n'est pas vérifiée, donc rien n'est écrit. La lecture est permise (liste). « jamman.py struktur <carte> » crée un rapport que vous pouvez transmettre.",
        'Speicherkarte des %s':
         'Carte mémoire du %s',
        'Struktur melden …':
         'Signaler la structure …',
        'Strukturbericht':
         'Rapport de structure',
        'Der Bericht ließ sich nicht schreiben: %s':
         "Le rapport n'a pas pu être écrit : %s",
        'Nur-Lesen-Modus: unbekanntes Gerät „%s“. Die Karte wird gelesen; geschrieben wird nichts, weil der Aufbau nicht belegt ist. Mit „Struktur melden …“ können Sie ihn belegen.':
         "Mode lecture seule : appareil inconnu « %s ». La carte est lue ; rien n'est écrit car la structure n'est pas vérifiée. « Signaler la structure … » fournit cette preuve.",
        'Bericht geschrieben: %s':
         'Rapport écrit : %s',
        'Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.':
         'Contient : les noms de dossiers et les fichiers XML, sans audio et sans vos textes (noms de patches remplacés par …). %d fichiers XML.',
        'Bitte schicken Sie diese Datei an mich oder hängen Sie sie an ein GitHub-Issue — dann kann ich den Aufbau prüfen und das Gerät freigeben.':
         "Envoyez-moi ce fichier ou joignez-le à un ticket GitHub — je pourrai alors vérifier la structure et valider l'appareil.",
        'Spalten (%s): %s':
         'Colonnes (%s) : %s',
        'Spalten (%s): Aufbau unvollständig':
         'Colonnes (%s) : structure incomplète',
        'Länge':
         'Durée',
        'fade':
         'fondu',
        'loop':
         'boucle',
        'single':
         'unique',
        "Fehler: auf '%s' liegt kein unterstützter JamMan-Geräteordner (%s). Das ist keine JamMan-Karte.":
         "  Erreur : '%s' ne contient aucun dossier d'appareil JamMan pris en charge (%s). Ce n'est pas une carte JamMan.",
        'beim Start automatisch nach Updates sehen':
         'chercher les mises à jour automatiquement au démarrage',
        'nach jedem Update automatisch prüfen':
         'vérifier automatiquement après chaque mise à jour',
        'Aktualisierung':
         'Mise à jour',
        'Selbsttest nach Update':
         'Test après mise à jour',
        'Fenster zu schmal für seinen Inhalt: %d von %d Pixel — bitte breiter ziehen':
         'Fenêtre trop étroit pour son contenu : %d sur %d pixels — élargissez-le',
        'abgeschnittene Beschriftung: %s':
         'libellé coupé : %s',
        'Die letzte Sicherung stammt aus dem anderen Speicher. Bitte oben umschalten und es noch einmal versuchen.':
         "La dernière sauvegarde vient de l'autre mémoire. Changez de mémoire en haut et réessayez.",
        'deutscher Text in der Anzeige: %s':
         "texte allemand resté dans l'affichage : %s",
        'sofort':
         'immédiatement',
        '%s   •   %s   •   Klick in die Spur springt dorthin':
         '%s   •   %s   •   cliquez sur la piste pour y aller',
        'Per E-Mail senden':
         'Envoyer par e-mail',
        'Mail im Mailprogramm vorbereitet — abschicken mußt du selbst.':
         "Courriel préparé dans votre logiciel de messagerie — à vous de l'envoyer.",
        'Mailprogramm ließ sich nicht öffnen.':
         "Le logiciel de messagerie ne s'est pas ouvert.",
        'Hier liegen einmal die Zusatzstarter und die Textdateien.\nDer Selbsttest im Werkzeug schreibt seinen Bericht dorthin, wo das Programm liegt: .jamman/.\n':
         'Ce dossier contient les lanceurs supplémentaires et les fichiers texte.\nLe test de fonctionnement écrit son rapport là où se trouve le programme : .jamman/.\n',
        '  Ordner aufgeräumt — das Werkzeug startet neu …':
         "  Dossiers rangés — l'outil redémarre …",
        'Diese Fassung liegt in einer anderen Ordneraufteilung. Bitte einmal von Hand herunterladen:\n%s':
         'Cette version utilise une autre arborescence. Merci de la télécharger une fois à la main :\n%s',
        'Diese Fassung des Werkzeugs kann sich nicht selbst aktualisieren (neue Ordneraufteilung). Bitte einmal von Hand herunterladen:\n%s':
         "Cette version de l'outil ne peut pas se mettre à jour elle-même (nouvelle arborescence). Merci de la télécharger une fois à la main :\n%s",
        'Als Issue vorbereiten':
         'Préparer comme ticket',
        'Issue im Browser vorbereitet — abschicken mußt du selbst.':
         "Ticket préparé dans le navigateur — à vous de l'envoyer.",
        'Browser ließ sich nicht öffnen.':
         "Le navigateur ne s'est pas ouvert.",
        'Nichts wird gesendet, solange du nicht selbst auf GitHub auf Absenden drückst.':
         "Rien n'est envoyé tant que vous ne cliquez pas vous-même sur Envoyer sur GitHub.",
        'Selbsttest':
         'Test de fonctionnement',
        'Selbsttest …':
         'Test de fonctionnement …',
        'Bericht des Selbsttests':
         'Rapport du test',
        'Kopieren':
         'Copier',
        'Kopieren ging nicht.':
         'La copie a échoué.',
        'In die Zwischenablage kopiert.':
         'Copié dans le presse-papiers.',
        'Das Werkzeug prüft sich selbst: es legt eine Testkarte an, drückt jeden Knopf und spielt die Dateibefehle durch.\n\nDabei gehen kurz Fenster auf und zu. Das Pedal bleibt unberührt, es wird nichts an deinen Karten geändert.\n\nDauert ein paar Sekunden. Fortfahren?':
         "L'outil se teste lui-même : il crée une carte de test, appuie sur chaque bouton et exécute les commandes de fichiers.\n\nDes fenêtres s'ouvrent et se ferment brièvement. Le pédalier n'est pas touché, rien n'est modifié sur vos cartes.\n\nQuelques secondes. Continuer ?",
        '  [Hinweis] Kein Fenster geprüft (%s): dieses Python hat Tk 8.5 — es lief das Textmenü.':
         '  [note] aucune fenêtre vérifiée (%s) : ce Python a Tk 8.5 — le menu texte a été utilisé.',
        '  Funktionstest des JamMan Card Tool':
         '  Test de fonctionnement du JamMan Card Tool',
        '  System:  %s':
         '  Système : %s',
        '  Python:  %s':
         '  Python : %s',
        '  Fassung: %s':
         '  Version : %s',
        '  Testkarte: %s':
         '  Carte de test : %s',
        '  [ok]   Befehl %s':
         '  [ok]   commande %s',
        '  [ok]   Befehl %s meldet: %s':
         '  [ok]   commande %s signale : %s',
        '  [FEHL] Befehl %s: %s':
         '  [ÉCHEC] commande %s : %s',
        '  [Hinweis] Ohne Tk kein Fenstertest: %s':
         '  [note] sans Tk, pas de test de fenêtre : %s',
        '  %d Befund(e) — bitte mitschicken (stehen auch in %s).':
         '  %d constat(s) — merci de les envoyer (ils sont aussi dans %s).',
        '  Ergebnis: keine Befunde.':
         '  Résultat : aucun constat.',
        '  [FEHL] Fenster (%s): %s':
         '  [ÉCHEC] fenêtre (%s) : %s',
        '  [ok]   Fenster (%s): %d Knöpfe, keine Ausnahme':
         '  [ok]   fenêtre (%s) : %d boutons, aucune exception',
        '%d Balkenfenster blieben offen':
         '%d fenêtres de progression restées ouvertes',
        'Fenster':
         'Fenêtre',
        'Abgebrochen — keine Datei geschrieben.':
         'Annulé — aucun fichier écrit.',
        'Es läuft noch eine Arbeit. Abbrechen und das Fenster schließen?\n\nDie unfertige Datei wird gelöscht.':
         "Une tâche est encore en cours. L'annuler et fermer la fenêtre ?\n\nLe fichier inachevé sera supprimé.",
        '%d von %d Patches gelesen':
         '%d sur %d patchs lus',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe aus dieser Sicherung zurückholen?\n\n  %s\n\nDie Aufnahme bleibt, wie sie ist. Der jetzige Stand wird vorher gesichert.':
         "Patch %d : restaurer nom, mode d'arrêt, rythme et lecture depuis cette sauvegarde ?\n\n  %s\n\nL'enregistrement reste tel quel. L'état actuel est sauvegardé avant.",
        'Patch%d aus dieser Sicherung wiederherstellen?\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen. Der jetzige Stand wird vorher gesichert.':
         "Restaurer le patch %d depuis cette sauvegarde ?\n\n  %s\n\nCela permet par exemple d'annuler un fondu. L'état actuel est sauvegardé avant.",
        'Einstellungen schreiben …':
         'écrire les réglages …',
        'Aufnahme bearbeiten …':
         "traiter l'enregistrement …",
        'fertig':
         'terminé',
        'einen Moment …':
         'un instant …',
        'Sicherung anlegen …':
         'créer une sauvegarde …',
        'Liste neu lesen …':
         'relire la liste …',
        '  Keine Eingabe möglich — Ende.':
         '  Aucune entrée possible — fin.',
        'Sicherungen löschen':
         'Supprimer les sauvegardes',
        '%d von %d gelöscht':
         '%d sur %d supprimés',
        '%d von %d gelöscht: %s':
         '%d sur %d supprimés : %s',
        'Alle normieren':
         'Tout normaliser',
        '%d von %d Patches: %s':
         '%d sur %d patchs : %s',
        'Sicherung anlegen':
         'Créer une sauvegarde',
        '%d von %d Dateien · %.0f MB':
         '%d sur %d fichiers · %.0f Mo',
        'Doppelklick spielt · Ziehen ordnet um · ⌘ zwei markieren, dann Tauschen · Esc hält an · ⌘O Karte, ⌘S Sicherungen, ⌘L Lücken':
         'Double-clic lit · glisser réordonne · ⌘ marque deux, puis Échanger · Échap arrête · ⌘O carte, ⌘S sauvegardes, ⌘L lacunes',
        'Setlist-Dateien':
         'Fichiers de setlist',
        'Anpassen':
         'Adapter',
        'Erst eine Datei markieren.':
         "Sélectionnez d'abord un fichier.",
        '%d Datei(en) angepasst, %d Zeile(n) umgeschrieben.':
         '%d fichier(s) adapté(s), %d ligne(s) réécrite(s).',
        'In diesem Ordner liegen keine Setlist-Dateien:\n%s\n\nBitte einen anderen Ordner wählen.':
         "Il n'y a pas de fichiers de setlist dans ce dossier :\n%s\n\nVeuillez choisir un autre dossier.",
        'Alle sind markiert — einzelne mit Klick abwählen.':
         'Tous sont sélectionnés — cliquez pour en désélectionner.',
        'betrifft Platz: %s':
         "concerne l'emplacement : %s",
        'Pegeländerung zurücknehmen':
         'Annuler le changement de niveau',
        'Es gibt noch keine Pegelprotokolle.':
         "Il n'y a pas encore de journaux de niveau.",
        'Pegelprotokolle':
         'Journaux de niveau',
        'Welche Änderung soll zurückgenommen werden?':
         'Quel changement faut-il annuler ?',
        'Erst ein Protokoll markieren.':
         "Sélectionnez d'abord un journal.",
        'Im Protokoll stehen keine Einträge.':
         'Le journal ne contient aucune entrée.',
        'Ein Patch (Platz %s)':
         'Un patch (emplacement %s)',
        '%d Patches':
         '%d patchs',
        '%s aus diesem Protokoll werden umgekehrt:\n\n%s\n\nNur diese Patches werden vorher gesichert. Fortfahren?':
         '%s de ce journal sont inversés :\n\n%s\n\nSeuls ces patchs sont sauvegardés avant. Continuer ?',
        'Pegeländerung zurücknehmen …':
         'Annuler le changement de niveau …',
        '  Trockenlauf — nichts geändert.':
         '  Essai à blanc — rien de modifié.',
        '  Jetziger Stand gesichert: %s':
         '  État actuel sauvegardé : %s',
        '  Einstellungen von Patch%d zurückgeholt.':
         '  Réglages du patch %d restaurés.',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe kommen aus dieser Sicherung zurück:\n\n  %s\n\nDie Aufnahme bleibt unberührt — sie war in dieser Sicherung nicht enthalten.\nDer jetzige Stand wird vorher gesichert.':
         "Patch %d : nom, mode d'arrêt, rythme et lecture reviennent de cette sauvegarde :\n\n  %s\n\nL'enregistrement reste intact — il ne faisait pas partie de cette sauvegarde.\nL'état actuel est sauvegardé avant.",
        'Patch%d wird aus dieser Sicherung wiederhergestellt:\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen.\nDer jetzige Stand wird vorher gesichert.':
         "Le patch %d est restauré depuis cette sauvegarde :\n\n  %s\n\nCela permet par exemple d'annuler un fondu.\nL'état actuel est sauvegardé avant.",
        'Patch%d zurückgeholt':
         'Patch %d restauré',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nPatches, die schon passen, bleiben unangetastet. Nur die\nübrigen werden vorher gesichert und neu geschrieben.\n\nFortfahren?':
         'Tous les patchs sont ramenés à la même sonie :\n\n  cible -18 dB de sonie, crête -1 dB au plus\n\nLes patchs déjà corrects ne sont pas touchés. Seuls les autres sont\nsauvegardés puis réécrits.\n\nContinuer ?',
        '  Nichts zu tun: alle Patches liegen bereits auf der Ziel-Lautheit.':
         '  Rien à faire : tous les patchs sont déjà au niveau cible.',
        '  Ein Patch war schon auf Ziel und blieb unverändert.':
         '  Un patch était déjà au niveau cible et est resté tel quel.',
        '  Schon auf Ziel: %d von %d Patches — unverändert gelassen.':
         '  Déjà au niveau cible : %d sur %d patchs — laissés tels quels.',
        '  %d Patches waren schon auf Ziel und blieben unverändert.':
         '  %d patchs étaient déjà au niveau cible et sont restés tels quels.',
        'Blättern':
         'Navigation',
        'Normierung zurücknehmen …':
         'Annuler la normalisation …',
        'auf −1 dB Spitze normalisieren':
         'normaliser à −1 dB crête',
        'Anhebung höchstens auf 0 dB':
         "limiter l'amplification à 0 dB au plus",
        'Sicherungen verwalten …':
         'Gérer les sauvegardes …',
        'Lücken bei den Plätzen %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         'Lacunes aux emplacements %s%s — « Combler les lacunes » fait avancer les emplacements suivants.',
        'Zurücknehmen (letzter Schritt)':
         'Annuler (dernière étape)',
        'Zurücknehmen':
         'Annuler',
        'ältere markieren':
         'marquer les plus anciennes',
        'Anderer Ordner …':
         'Autre dossier …',
        'Neue Sicherung anlegen (alles sichern)':
         'Créer une nouvelle sauvegarde (tout)',
        'Sicherungen …':
         'Sauvegardes …',
        'Ordner mit den Sicherungen':
         'Dossier des sauvegardes',
        'Sammelsicherung':
         'Sauvegarde de secours',
        'Rette':
         'Sauvegarde',
        'Es gibt keine unersetzlichen Patches.':
         "Il n'y a pas de patchs irremplaçables.",
        '%d Patches wurden nach %s kopiert. Die Zuordnung steht dort in Rettung.txt. Jetzt lassen sich die alten Sicherungen löschen.':
         '%d patchs ont été copiés vers %s. La correspondance se trouve dans Rettung.txt. Les anciennes sauvegardes peuvent maintenant être supprimées.',
        'Nur die unersetzlichen Patches retten':
         'Sauver seulement les patchs irremplaçables',
        '⟨%d Patch nur hier⟩':
         '⟨%d patch seulement ici⟩',
        '%d markiert · %.1f MB':
         '%d sélectionnée(s) · %.1f Mo',
        'nichts markiert':
         'rien de sélectionné',
        'Vorgabe: neueste 3 und letzte 7 Tage behalten':
         'Par défaut : garder les 3 plus récentes et les 7 derniers jours',
        'Fertig. Ordner:\n%s':
         'Terminé. Dossier :\n%s',
        'Alte Sicherungen in diesem Ordner ansehen und löschen?':
         'Voir les anciennes sauvegardes dans ce dossier et en supprimer ?',
        'Lücke bei Platz %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         "Lacune à l'emplacement %s%s — « Combler les lacunes » fait avancer les emplacements suivants.",
        'Platz %s ist nicht belegt — die Liste hat eine Lücke.':
         "L'emplacement %s n'est pas occupé — la liste a une lacune.",
        'Lücken schließen':
         'Combler les lacunes',
        'Patch%d → Patch%d':
         'Patch%d → Patch%d',
        '%d Patches rücken nach vorn: %s%s\n\nDie Reihenfolge bleibt, nur die Platznummern ändern sich. Die Zuordnung wird vorher gesichert. Setlist-Dateien, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         "%d patches avancent : %s%s\n\nL'ordre reste le même, seuls les numéros d'emplacement changent. La correspondance est sauvegardée avant. Les fichiers de setlist qui renvoient à des numéros ne correspondront plus. Continuer ?",
        'Aufgerückt: %d Patches':
         'Avancés : %d patches',
        'Sollen vorhandene Setlist-Dateien auf die neuen Platznummern umgeschrieben werden?':
         'Faut-il réécrire les fichiers de setlist existants avec les nouveaux numéros ?',
        'Ordner mit Setlist-Dateien':
         'Dossier avec fichiers de setlist',
        'Setlists angepaßt: %d Datei(en)':
         'Setlists adaptées : %d fichier(s)',
        'Fehler: Platz %d ist belegt (%s).':
         "Erreur : l'emplacement %d est occupé (%s).",
        'Beenden':
         'Quitter',
        'Dem Python fehlen die Zertifikate. Zwei Wege: im Python-Ordner „Install Certificates.command“ ausführen — oder „pip install certifi“ — oder die Datei von der Release-Seite von Hand laden.':
         'Il manque les certificats à Python. Deux solutions : exécuter « Install Certificates.command » dans le dossier Python, ou « pip install certifi » — ou télécharger le fichier depuis la page de publication.',
        'Achtung: das Zertifikat ließ sich nicht prüfen — die Datei wurde ohne Prüfung geladen.':
         "Attention : le certificat n'a pas pu être vérifié — le fichier a été téléchargé sans contrôle.",
        'Die Datei ist unvollständig: %d statt %d Bytes.':
         'Le fichier est incomplet : %d au lieu de %d octets.',
        'Neue Fassung %s wird geholt …':
         'Récupération de la version %s …',
        'Fassung %s ist installiert. Bitte das Fenster schließen und neu starten.':
         'La version %s est installée. Veuillez fermer la fenêtre et redémarrer.',
        'Update beim Start':
         'Mise à jour au démarrage',
        'Die Karte ist eingebunden, aber das Dateisystem liefert\nLesefehler:\n\n%s\n\nDas liegt an der Karte oder am Einhängepunkt, nicht am\nWerkzeug.\n\nErste Schritte:\n  - Karte im JamMan einschalten (das Pedal prüft sie)\n  - dmesg | tail -20\n  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n    bzw. sudo exfatfsck <gerät>':
         "La carte est montée, mais le système de fichiers signale des erreurs de lecture :\n\n%s\n\nCela vient de la carte ou du point de montage, pas de l'outil.\n\nPremiers pas :\n  - mettre la carte dans la JamMan et l'allumer (la pédale la vérifie)\n  - dmesg | tail -20\n  - démonter et vérifier : sudo fsck.vfat -n <périphérique>\n    ou sudo exfatfsck <périphérique>",
        'kein Pedal':
         'pas de pédale',
        'Beschreibung …':
         'Description …',
        'Rückwärts: ohne ffmpeg keine Hörprobe — das Pedal spielt es trotzdem rückwärts.':
         "Inversé : sans ffmpeg pas d'écoute — la pédale le joue quand même à l'envers.",
        'Tempo':
         'Tempo',
        'Suchen …':
         'Rechercher …',
        'Text im Namen (leer lassen = alles zeigen):':
         'Texte dans le nom (vide = tout afficher) :',
        'Erst eine Karte oder eine geholte Kopie öffnen — dann läßt sich suchen.':
         "Ouvrez d'abord une carte ou une copie récupérée — ensuite la recherche fonctionne.",
        'Suchen':
         'Rechercher',
        'Suche fehlgeschlagen: %s':
         'Recherche échouée : %s',
        'Suchen:':
         'Rechercher :',
        'Karte:':
         'Carte :',
        'Meldungen stehen im Fenster; Fehler zusätzlich in jamman-fehler.log.':
         "Les messages s'affichent dans la fenêtre ; les erreurs aussi dans jamman-fehler.log.",
        'Firmware-Dateien jetzt ins Pedal schreiben? Ein Abbruch mittendrin kann das Pedal unbrauchbar machen. [ja/nein] ':
         'Écrire maintenant les fichiers de firmware dans la pédale ? Une interruption peut la rendre inutilisable. [oui/non] ',
        'Zielordner für die Firmware':
         'Dossier cible de la firmware',
        'Ordner mit der geholten Firmware':
         'Dossier de la firmware récupérée',
        'In diesem Ordner liegt keine Firmware (usr/JamManUI, usr/loopengine …).':
         'Ce dossier ne contient pas de firmware (usr/JamManUI, usr/loopengine …).',
        'Firmware übernehmen':
         'Appliquer la firmware',
        'Firmware übernehmen …':
         'Appliquer la firmware …',
        'Firmware dieses Pedals holen …':
         'Récupérer la firmware de cette pédale …',
        '%s kam unvollständig (%d von %d Bytes).':
         '%s est arrivé incomplet (%d sur %d octets).',
        '%d Firmware-Dateien werden jetzt in das Pedal geschrieben. Vorher wird der jetzige Stand gesichert.\n\nEin Abbruch mittendrin kann das Pedal unbrauchbar machen — es gibt keinen Weg zurück.\n\nWirklich schreiben?':
         "%d fichiers de firmware vont être écrits dans la pédale. L'état actuel est sauvegardé avant.\n\nUne interruption peut rendre la pédale inutilisable — aucun retour.\n\nÉcrire vraiment ?",
        'Sichere den jetzigen Stand …':
         "Sauvegarde de l'état actuel …",
        'Gesichert: %d Dateien nach %s':
         'Sauvegardé : %d fichiers dans %s',
        'Firmware geholt: %d Dateien':
         'Firmware récupérée : %d fichiers',
        'Firmware geholt:\n%s\n%d Dateien':
         'Firmware récupérée :\n%s\n%d fichiers',
        'Fertig: %d Dateien geschrieben':
         'Terminé : %d fichiers écrits',
        'Nicht bestätigt: %d':
         'Non confirmé : %d',
        'Firmware geschrieben: %d Dateien\nSicherung: %s\nJetzt das Pedal aus- und wieder einschalten.':
         'Firmware écrite : %d fichiers\nSauvegarde : %s\nÉteignez puis rallumez la pédale.',
        'Schreibe':
         'Écriture',
        'Hole':
         'Récupération',
        'JamList aus JamManager XT holen …':
         'Récupérer la JamList de JamManager XT …',
        'Setlist als JamList ablegen …':
         'Enregistrer la setlist comme JamList …',
        'Setlist speichern':
         'Enregistrer la setlist',
        'Bibliothek von JamManager XT':
         'Bibliothèque de JamManager XT',
        'JamList wählen':
         'Choisir une JamList',
        'In dieser Bibliothek liegt keine JamList.':
         'Cette bibliothèque ne contient aucune JamList.',
        'Geholt: %s\n%d Plätze\n%s':
         'Récupéré : %s\n%d emplacements\n%s',
        'JamList ablegen':
         'Enregistrer la JamList',
        '%s%s%s als neue JamList eintragen? Die Bibliothek wird dabei verändert — vorher eine Kopie anlegen.':
         "Ajouter %s%s%s comme nouvelle JamList ? La bibliothèque sera modifiée — faites une copie d'abord.",
        'JamList %s angelegt (id %d, %d Plätze)':
         'JamList %s créée (id %d, %d emplacements)',
        'Keine JamList mit dem Namen %s.':
         'Aucune JamList nommée %s.',
        'belegte Plätze':
         'emplacements occupés',
        'Geschrieben: %s (%d Plätze)':
         'Écrit : %s (%d emplacements)',
        'Übernehmen':
         'Appliquer',
        'Speicherabbild':
         'Image mémoire',
        'Speicherabbild speichern':
         "Enregistrer l'image mémoire",
        'Speicherabbild aufnehmen …':
         'Prendre une image mémoire …',
        'Speicherabbilder vergleichen …':
         'Comparer les images mémoire …',
        'Speicherabbilder vergleichen':
         'Comparer les images mémoire',
        'Erstes Speicherabbild':
         'Première image mémoire',
        'Zweites Speicherabbild':
         'Deuxième image mémoire',
        'Nur im ersten Bild: %d':
         'Seulement dans la première image : %d',
        'Nur im zweiten Bild: %d':
         'Seulement dans la deuxième image : %d',
        'Gleicher Pfad, andere Größe: %d':
         'Même chemin, taille différente : %d',
        'Die beiden Bilder sind gleich.':
         'Les deux images sont identiques.',
        'Erstes:':
         'Première :',
        'Zweites:':
         'Deuxième :',
        'Aufruf:':
         'Appel :',
        'Lese den Verzeichnisbaum …':
         "Lecture de l'arborescence …",
        'Speicherabbild: %d Einträge':
         'Image mémoire : %d entrées',
        'Speicherabbild geschrieben:\n%s':
         'Image mémoire enregistrée :\n%s',
        'Speicherabbild geschrieben: %d Einträge nach %s':
         'Image mémoire enregistrée : %d entrées dans %s',
        'Dateien für „speicherabbild“ und „vergleich“':
         'Fichiers pour « speicherabbild » et « vergleich »',
        'Sortierung':
         'Tri',
        '%d von %d Patches passen zu „%s“.':
         '%d patches sur %d correspondent à « %s ».',
        'Beschreibung':
         'Description',
        'Interpret':
         'Artiste',
        'Genre':
         'Genre',
        'Stichwörter':
         'Mots-clés',
        'Bewertung':
         'Note',
        'Tempo (BPM)':
         'Tempo (BPM)',
        'Die Datei %s fehlt.':
         'Le fichier %s manque.',
        'Setlists anpassen':
         'Adapter les setlists',
        '%d Setlist-Dateien in %s an die neuen Plätze anpassen? Jede Datei wird vorher daneben gesichert.':
         "Adapter %d fichiers de setlist dans %s aux nouveaux emplacements ? Chaque fichier est d'abord sauvegardé à côté.",
        'Keine Setlist nannte einen bewegten Platz.':
         'Aucune setlist ne mentionnait un emplacement déplacé.',
        'Setlists nicht angepaßt: ':
         'Setlists non adaptées : ',
        'Setlists angepaßt: ':
         'Setlists adaptées : ',
        'Plätze aufrücken':
         'Combler les trous',
        'Plätze aufrücken …':
         'Combler les trous …',
        'Es gibt keine Lücken — die Plätze sind schon lückenlos belegt.':
         "Il n'y a pas de trous — les emplacements sont déjà contigus.",
        '%d Patches rücken in %s nach vorn: %s%s\n\nJeder bewegte Platz wird vorher auf den Rechner gesichert. Setlists im Pedal, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d patches remontent dans %s : %s%s\n\nChaque emplacement déplacé est sauvegardé avant. Les setlists de la pédale qui pointent sur des numéros ne correspondront plus. Continuer ?',
        'Rücke auf':
         'Remontée',
        'Fertig: %d Plätze aufgerückt, %d vorher gesichert':
         'Terminé : %d emplacements remontés, %d sauvegardés avant',
        'Aufgerückt: %d Plätze':
         'Remontés : %d emplacements',
        'Kopie auf dem Rechner — Änderungen hier wirken nicht auf das Pedal.':
         "Copie sur l'ordinateur — les modifications ici n'affectent pas la pédale.",
        'Patch%d wird aus der Kopie auf dem Rechner entfernt. Das Pedal bleibt unverändert. Fortfahren?':
         "Patch%d sera supprimé de la copie sur l'ordinateur. La pédale reste inchangée. Continuer ?",
        'Patch%d wird von der Karte entfernt. Vorher wird er auf dem Rechner gesichert. Fortfahren?':
         "Patch%d sera supprimé de la carte. Il est d'abord sauvegardé sur l'ordinateur. Continuer ?",
        'Ja':
         'Oui',
        'Nein':
         'Non',
        'Dem Python fehlen die Zertifikate. Auf macOS hilft „Install Certificates.command“ im Python-Ordner, oder: pip install certifi':
         "Python n'a pas de certificats. Sous macOS, lancez « Install Certificates.command » dans le dossier Python, ou : pip install certifi",
        'GitHub ist nicht erreichbar: %s':
         'GitHub est injoignable : %s',
        'unbekannter Grund':
         'raison inconnue',
        'Gelöscht: %s':
         'Supprimé : %s',
        'Fehler:':
         'Erreur :',
        'Platz löschen':
         "Supprimer l'emplacement",
        'Welcher Platz soll gelöscht werden? (z. B. 30 oder Patch30)':
         'Quel emplacement supprimer ? (p. ex. 30 ou Patch30)',
        'Platz löschen …':
         "Supprimer l'emplacement …",
        '%s ist nicht belegt — nichts zu löschen.':
         "%s n'est pas occupé — rien à supprimer.",
        '%s wird aus %s gelöscht. Das läßt sich nicht rückgängig machen. Fortfahren?':
         '%s sera supprimé de %s. Action irréversible. Continuer ?',
        'Lösche':
         'Suppression',
        'Gelöscht:':
         'Supprimé :',
        'Fehler beim Löschen:':
         'Erreur lors de la suppression :',
        'Die Datei ist %s lang — das Pedal nimmt höchstens 10 Minuten je Loop an. Bitte im Hauptfenster kürzen oder teilen.':
         'Le fichier dure %s — la pédale accepte au maximum 10 minutes par boucle. Veuillez le raccourcir ou le diviser dans la fenêtre principale.',
        'Dieser Ordner ist nicht lesbar: %s':
         "Ce dossier n'est pas lisible : %s",
        'Auswahl:':
         'Sélection :',
        'Pegelprotokoll wählen':
         'Choisir le journal de niveau',
        'Audiodatei zum Teilen auswählen':
         'Choisir le fichier audio à diviser',
        'Dateiname:':
         'Nom du fichier :',
        'Speichern':
         'Enregistrer',
        'Bitte einen Dateinamen angeben.':
         'Veuillez saisir un nom de fichier.',
        'Überschreiben':
         'Écraser',
        '%s gibt es schon. Ersetzen?':
         '%s existe déjà. Remplacer ?',
        'Speicherkarte des JamMan wählen':
         'Choisir la carte du JamMan',
        'Zielordner für die Sicherung':
         'Dossier cible de la sauvegarde',
        'Dateien':
         'Fichiers',
        'Nach oben':
         'Dossier parent',
        'Öffnen':
         'Ouvrir',
        'Bitte eine Datei auswählen.':
         'Veuillez choisir un fichier.',
        'Alle Dateien':
         'Tous les fichiers',
        'Die Datei konnte nicht geschrieben werden: %s':
         "Le fichier n'a pas pu être écrit : %s",
        'ohne':
         'aucun',
        'ende':
         'fin',
        'klick':
         'clic',
        '%d Patches ohne Ton — nur die Beschreibung ist geholt.':
         '%d patches sans audio — seule la description a été récupérée.',
        'Ganzen Ordner aufs Pedal …':
         'Tout le dossier vers la pédale …',
        'Ordner mit Patch-Ordnern wählen':
         'Choisir un dossier contenant des dossiers de patch',
        'Ganzen Ordner aufs Pedal':
         'Tout le dossier vers la pédale',
        '%d Patch-Ordner werden in %s geschrieben. Belegte Plätze werden vorher gesichert, unveränderte übersprungen. Fortfahren?':
         '%d dossiers de patch seront écrits dans %s. Les emplacements occupés sont sauvegardés avant, les inchangés ignorés. Continuer ?',
        'internen Speicher':
         'la mémoire interne',
        'der Karte':
         'la carte',
        'Im Ordner liegen keine Patch-Ordner (erwartet: Patch01 … Patch200).':
         'Le dossier ne contient aucun dossier de patch (attendu : Patch01 … Patch200).',
        'Fertig: %d Patches geschrieben, %d übersprungen, %d vorher gesichert':
         'Terminé : %d patches écrits, %d ignorés, %d sauvegardés avant',
        'Übertragen: %d Dateien · unverändert übersprungen: %d':
         'Transféré : %d fichiers · ignorés inchangés : %d',
        'Zielplatz auf dem Pedal (z. B. 17 oder Patch17):':
         'Emplacement cible sur la pédale (p. ex. 17 ou Patch17) :',
        'Unerwarteter Fehler':
         'Erreur inattendue',
        'Das Werkzeug wurde unerwartet beendet.':
         "L'outil s'est arrêté de façon inattendue.",
        'Protokoll:':
         'Journal :',
        'Auswahl kopieren':
         'Copier la sélection',
        'Nichts markiert — bitte zuerst Text markieren.':
         "Rien de sélectionné — sélectionnez d'abord du texte.",
        'Anleitung speichern':
         'Enregistrer les instructions',
        'Gespeichert.':
         'Enregistré.',
        'Alles kopieren':
         'Tout copier',
        'Als Datei speichern':
         'Enregistrer comme fichier',
        'Anleitung':
         'Instructions',
        'Zum Umwandeln fehlt ffmpeg.':
         'ffmpeg manque pour la conversion.',
        'Die Programmdateien lassen sich hier nicht ersetzen: %s':
         'Les fichiers du programme ne peuvent pas être remplacés ici : %s',
        'Unerwarteter Fehler: %s':
         'Erreur inattendue : %s',
        'Aktualisieren':
         'Mettre à jour',
        'Die neueste Fassung wird von GitHub geladen und ersetzt die Programmdateien. Ihre Einstellungen und Ihre Karte bleiben unberührt. Fortfahren?':
         'La dernière version est téléchargée depuis GitHub et remplace les fichiers du programme. Vos réglages et votre carte ne sont pas touchés. Continuer ?',
        'Jetzt aktualisieren?':
         'Mettre à jour maintenant ?',
        'Aktualisiert':
         'Mis à jour',
        'Nicht aktualisiert':
         'Non mis à jour',
        'Fenster jetzt schließen?':
         'Fermer la fenêtre maintenant ?',
        'GitHub ist nicht erreichbar.':
         'GitHub est injoignable.',
        'Sie benutzen bereits die neueste Fassung (%s).':
         'Vous utilisez déjà la dernière version (%s).',
        'Zur Fassung %s gibt es keine Datei zum Herunterladen.':
         "La version %s n'a pas de fichier à télécharger.",
        'Lade Fassung %s …':
         'Téléchargement de la version %s …',
        'Herunterladen fehlgeschlagen: %s':
         'Téléchargement échoué : %s',
        'Die heruntergeladene Datei ist kein gültiges ZIP.':
         "Le fichier téléchargé n'est pas un ZIP valide.",
        'Die Datei ist beschädigt (%s).':
         'Le fichier est endommagé (%s).',
        'In der Datei fehlt jamman.py.':
         'Le fichier ne contient pas jamman.py.',
        'Die Datei gehört nicht zur Fassung %s.':
         "Le fichier n'appartient pas à la version %s.",
        'Fassung %s ist installiert (%d Dateien). Bitte das Fenster schließen und neu starten.':
         'La version %s est installée (%d fichiers). Fermez la fenêtre et relancez le programme.',
        'Die Karte hängt nicht im Rechner — nichts auszuwerfen. Steckt sie im Pedal, führt das Pedal sie nicht als Laufwerk.':
         "La carte n'est pas dans l'ordinateur — rien à éjecter. Si elle est dans la pédale, celle-ci ne la présente pas comme un lecteur.",
        'Auswerfen ging nicht. Bitte im Dateimanager auf „Auswerfen" klicken.':
         "L'éjection a échoué. Utilisez « Éjecter » dans le gestionnaire de fichiers.",
        'Keine Karte im Rechner gefunden.':
         'Aucune carte trouvée dans l\'ordinateur.',
        'Karte im Rechner:  „Karte wählen“ drücken.':
         'Carte dans l\'ordinateur : appuyez sur « Choisir la carte ».',
        'Karte im Pedal:    oben rechts „Pedal über USB“ — dort sind Karte und interner Speicher erreichbar.':
         'Carte dans la pédale : en haut à droite « Pédale par USB » — la carte et la mémoire interne y sont accessibles.',
        'Speicherkarte': 'Carte mémoire',
        'Kopie vom Pedal': 'copie de la pédale',
        'Speicherkarte des JamMan Stereo': 'Carte mémoire du JamMan Stereo',
        'Im Pedal steckt keine Karte.': 'Aucune carte dans la pédale.',
        'Karte einlegen und dann „Karte wählen“ oder „Neu laden“ drücken.':
         'Insérez la carte, puis appuyez sur « Choisir la carte » ou « Recharger ».',
        'keine Karte':
         'aucune carte',
        'Dies ist eine Kopie vom Pedal, nicht die Karte selbst. Über „Karte wählen“ die Karte öffnen.':
         'Ceci est une copie prise sur la pédale, pas la carte elle-même. Utilisez « Choisir la carte ».',
        'JamMan Card Tool': 'JamMan Card Tool',
        'Karte und interner Speicher': 'carte et mémoire interne',
        '(für Rückgängig nötig)':
         '(nécessaire pour Annuler)',
        'Darunter ist die Sicherung für Rückgängig. Danach läßt sich der letzte Schreibvorgang nicht mehr zurücknehmen. Trotzdem löschen?':
         'Cela inclut la sauvegarde pour Annuler. La dernière écriture ne pourra plus être annulée. Supprimer quand même ?',
        'Sicherungen aufräumen …':
         'Nettoyer les sauvegardes …',
        'Sicherungen aufräumen':
         'Nettoyer les sauvegardes',
        'Sicherungen im Zielordner':
         'Sauvegardes dans le dossier cible',
        '%d Sicherungen · %.1f MB':
         '%d sauvegardes · %.1f Mo',
        'Neueste behalten:':
         'Conserver les plus récentes :',
        'Markierte löschen':
         'Supprimer la sélection',
        'Erst Sicherungen in der Liste markieren.':
         'Sélectionnez d\'abord des sauvegardes.',
        '%d Sicherungen mit %.1f MB werden endgültig gelöscht. Fortsetzen?':
         '%d sauvegardes (%.1f Mo) seront définitivement supprimées. Continuer ?',
        '%d Sicherungen konnten nicht gelöscht werden.':
         '%d sauvegardes n\'ont pas pu être supprimées.',
        'Der Zielordner ist noch nicht angelegt.':
         'Le dossier cible n\'existe pas encore.',
        'Im Zielordner liegen keine Sicherungen.':
         'Il n\'y a aucune sauvegarde dans le dossier cible.',
        'Alle auswählen':
         'Tout sélectionner',
        'Nichts markiert. Alle %d Patches in die Setlist?':
         'Rien de sélectionné. Mettre les %d patches dans la setlist ?',
        'Warte auf das Ende des laufenden Vorgangs …':
         'Attente de la fin de l\'opération en cours …',
        'Der Vorgang reagiert nicht — Fenster schließen und neu öffnen.':
         'L\'opération ne répond pas — fermez la fenêtre et rouvrez-la.',
        'Hole Beschreibungen': 'Lecture des descriptions',
        'Es wurde kein Zielplatz eingegeben.':
         'Aucun emplacement cible n\'a été saisi.',
        'Der Zielplatz muß wie Patch01 … Patch200 heißen, nicht „%s“.':
         'L\'emplacement doit s\'appeler Patch01 … Patch200, et non « %s ».',
        'OK': 'OK',
        'Ein Vorgang läuft noch — bitte abwarten.':
         'Une opération est encore en cours — veuillez patienter.',
        'Verbindung neu aufbauen':
         'Reconnecter',
        'Verbindung wird neu aufgebaut …':
         'Reconnexion …',
        'Lesen und Schreiben über USB. Vor jedem Überschreiben wird der alte Platz auf den Rechner gesichert.':
         'Lecture et écriture par USB. Avant tout écrasement, l\'ancien emplacement est sauvegardé sur l\'ordinateur.',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller. Schreiben verändert die Karte — vorher wird gesichert.':
         'Par USB, le débit est d\'environ 1,3 Mo/s. Pour une carte entière, un lecteur est bien plus rapide. L\'écriture modifie la carte — sauvegarde au préalable.',
        'Audio in den Speicher schreiben …': 'Écrire l\'audio dans la mémoire …',
        'Beim JamMan Solo XT behält der FS3X seine Grundfunktionen. Eine eigene Belegung speichert das Pedal zwar, wertet sie aber nicht aus — deshalb wird sie hier nicht angeboten.':
         'Sur le JamMan Solo XT, le FS3X garde ses fonctions de base. La pédale enregistre bien une affectation personnalisée mais ne l\'exploite pas ; elle n\'est donc pas proposée ici.',
        'Änderungen wirken nach einem Neustart des Pedals.': 'Les modifications prennent effet après un redémarrage de la pédale.',
        'gewünscht': 'demandé',
        'Pedal meldet': 'la pédale renvoie',
        '(nicht belegt)':
         '(non attribué)',
        'UndoRedo = Rückgängig/Wiederholen · Reverse = Rückwärts · Tempo = Tempo antippen':
         'UndoRedo = annuler/rétablir · Reverse = en arrière · Tempo = taper le tempo',
        'Sichern': 'Enregistrer',
        'Schreiben': 'Écrire',
        'Setlists': 'Setlists',
        'Pedal': 'Pédale',
        'Ins Hauptfenster holen …': 'Charger dans la fenêtre principale …',
        'Einzelnen Patch sichern …': 'Enregistrer un seul patch …',
        'Patch-Ordner aufs Pedal …': 'Dossier de patch sur la pédale …',
        'Übernommen.': 'Appliqué.',
        'Pedal spielt jetzt:': 'La pédale joue maintenant :',
        'Audio aufs Pedal …':
         'Audio sur la pédale …',
        'Audio aufs Pedal':
         'Audio sur la pédale',
        'Audiodatei wählen':
         'Choisir un fichier audio',
        'Audio':
         'Audio',
        'Sichere den alten Platz …':
         'Sauvegarde de l\'ancien emplacement …',
        'Wandle die Datei …':
         'Conversion du fichier …',
        'Umwandeln fehlgeschlagen — die Datei muß 44,1 kHz, 16 Bit, Stereo werden.':
         'Conversion échouée — le fichier doit devenir 44,1 kHz, 16 bits, stéréo.',
        'Leere den Platz …': 'Vidage de l\'emplacement …',
        'Pedal spielt jetzt Bank/Patch:': 'La pédale joue maintenant banque/patch :',
        'Karte erkannt:': 'Carte détectée :',
        'Als ZIP sichern …':
         'Enregistrer en ZIP …',
        'Als ZIP sichern':
         'Enregistrer en ZIP',
        'Sichere als ZIP …':
         'Enregistrement en ZIP …',
        'Sichere:':
         'Enregistrement :',
        'Fehler beim Sichern:':
         'Erreur d\'enregistrement :',
        'Die Setlist ist leer.':
         'La setlist est vide.',
        'Erst Patches in der Liste markieren (⌘-Klick).':
         'Sélectionnez d\'abord des patches dans la liste (⌘-clic).',
        'Es gibt nichts rückgängig zu machen.':
         'Il n\'y a rien à annuler.',
        'Fußschalter …':
         'Commande au pied …',
        'Fußschalter':
         'Commande au pied',
        'Tastenbelegung des Pedals':
         'Affectation des commandes au pied de la pédale',
        'Werte sind Geräteeinstellungen — nur ändern, wenn du weißt, was sie bedeuten.':
         'Ce sont des réglages de l\'appareil — ne les modifiez que si vous savez ce qu\'ils font.',
        'JamMan':
         'JamMan',
        'Karte':
         'carte',
        'intern':
         'interne',
        'Liste als Textdatei sichern':
         'Enregistrer la liste en fichier texte',
        'Liste als Textdatei …':
         'Liste en fichier texte …',
        'Mitlaufen':
         'Suivi',
        'Mitlaufen an — am Pedal einen Patch wählen.':
         'Suivi activé — choisissez un patch sur la pédale.',
        'Mitlaufen aus.':
         'Suivi désactivé.',
        'Rückgängig':
         'Annuler',
        'Setlist holen …':
         'Charger la setlist …',
        'Setlist laden':
         'Charger la setlist',
        'Setlist sichern …':
         'Enregistrer la setlist …',
        'Setlist sichern':
         'Enregistrer la setlist',
        'Setlist.txt':
         'Setlist.txt',
        'Stelle wieder her:':
         'Restauration :',
        '%s wird durch die Sicherung ersetzt. Fortsetzen?':
         '%s sera remplacé par la sauvegarde. Continuer ?',
        'Ordner des Patches wählen':
         'Choisir le dossier du patch',
        'Aufs Pedal schreiben':
         'Écrire sur la pédale',
        '%s ist schon belegt. Der Patch wird vorher auf den Rechner gesichert und dann überschrieben. Fortsetzen?':
         '%s est déjà occupé. Ce patch sera d\'abord enregistré sur l\'ordinateur, puis écrasé. Continuer ?',
        'Schreibe aufs Pedal …':
         'Écriture sur la pédale …',
        'Schreibe:':
         'Écriture :',
        'Fehler beim Schreiben:':
         'Erreur d\'écriture :',
        'Patch aufs Pedal schreiben …':
         'Écrire le patch sur la pédale …',
        '(Vorschlag)': '(proposition)',
        'Ziel:': 'Destination :',
        'noch nicht gewählt': 'pas encore choisi',
        'Ziel ändern …': 'Changer la destination …',
        'Aufräumen':
         'Nettoyer',
        'Die angefangene Kopie entfernen? Nur die gerade geholten Dateien werden gelöscht, der Ordner bleibt.':
         'Supprimer la copie inachevée ? Seuls les fichiers venant d\'être récupérés sont supprimés ; le dossier reste.',
        'Aufgeräumt:':
         'Nettoyé :',
        'Hole Beschreibungen …': 'Lecture des descriptions …',
        'Wird abgebrochen …': 'Annulation …',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller — steckt die Karte im Pedal, bleibt nur dieser Weg.':
         'Par USB, le débit est d\'environ 1,3 Mo/s. Pour une carte entière, un lecteur de cartes est bien plus rapide — si la carte est dans la pédale, c\'est la seule voie.',
        'Abbrechen': 'Annuler',
        'Abgebrochen.': 'Annulé.',
        'Fehler beim Öffnen:': 'Erreur à l\'ouverture :',
        'Alle ins Hauptfenster holen …': 'Tout charger dans la fenêtre principale …',
        'Zielordner wählen': 'Choisir le dossier de destination',
        'Hole Patch': 'Lecture du patch',
        'Fertig:': 'Terminé :',
        'Im Hauptfenster öffnen?': 'Ouvrir dans la fenêtre principale ?',
        'Gesichert': 'Enregistré',
        'Pedal über USB': 'Pédale par USB',
        'Das USB-Modul fehlt: greenglass.py muß neben jamman.py liegen.': 'Le module USB manque : greenglass.py doit se trouver à côté de jamman.py.',
        'Gesichert nach:': 'Enregistré dans :',
        'Interner Speicher': 'Mémoire interne',
        'SD-Karte': 'Carte SD',
        'Kein JamMan gefunden — Pedal einschalten und Kabel prüfen.': 'Aucun JamMan trouvé — allumez la pédale et vérifiez le câble.',
        '(ohne Namen)': '(sans nom)',
        'Fehler beim Lesen:': 'Erreur de lecture :',
        'Erst „Neu laden“ drücken.': 'Appuyez d\'abord sur « Recharger ».',
        'Lädt …': 'Chargement …',
        'Nur lesen: das Pedal wird nicht verändert.': 'Lecture seule : la pédale n\'est pas modifiée.',
        'Ordner zum Sichern wählen': 'Choisir un dossier de sauvegarde',
        'Patch sichern …': 'Enregistrer le patch …',
        'Verbinde mit dem Pedal …': 'Connexion à la pédale …',
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
        "BEFEHL": "COMMANDE",
        "  Auswahl: ": "  Choix : ",
        "  Anderer Pfad (leer = beenden): ": "  Autre chemin (vide = quitter) : ",
        "  Patch-Nummer: ": "  Numéro du patch : ",
        "  Name (leer = Dateiname): ": "  Nom (vide = nom du fichier) : ",
        "  Stopp (fade/instant, leer = wie Vorlage): ": "  Arrêt (fade/instant, vide = comme le modèle) : ",
        "  Wiedergabe (loop/single, leer = wie Vorlage): ": "  Lecture (loop/single, vide = comme le modèle) : ",
        "  Pfad zur Audiodatei: ": "  Chemin du fichier audio : ",
        "  Stopp (fade/instant, leer = lassen): ": "  Arrêt (fade/instant, vide = garder) : ",
        "  Wiedergabe (loop/single, leer = lassen): ": "  Lecture (loop/single, vide = garder) : ",
        "  Neuer Name (leer = lassen): ": "  Nouveau nom (vide = garder) : ",
        "  Wirklich schreiben? (j/n): ": "  Vraiment écrire ? (o/n) : ",
        "  Welcher Patch: ": "  Quel patch : ",
        "  An welche Stelle: ": "  À quel emplacement : ",
        "  Wirklich verschieben? (j/n): ": "  Vraiment déplacer ? (o/n) : ",
        "  Zielordner (leer = ~/JamMan-Sicherung): ": "  Dossier cible (vide = ~/JamMan-Sicherung) : ",
        "  Wirklich löschen? (j/n): ": "  Vraiment supprimer ? (o/n) : ",
        "Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay).": "Aucun lecteur trouvé (aplay, paplay, mpv ou ffplay).",
        "Fehler: im Protokoll stehen keine Einträge.": "Erreur : le protocole ne contient aucune entrée.",
        "Fehler: Datei ließ sich nicht umwandeln.": "Erreur : le fichier n'a pas pu être converti.",
        "Fehler: kein vollständiger Patch als Vorlage gefunden.": "Erreur : aucun patch complet trouvé comme modèle.",
        "Fehler: nichts angegeben (--stopp, --rhythmus, --wiedergabe, --name oder --fade).": "Erreur : rien d'indiqué (--stopp, --rhythmus, --wiedergabe, --name ou --fade).",
        "Fehler: '%s' ist keine lesbare Audiodatei.": "Erreur : '%s' n'est pas un fichier audio lisible.",
        "Fehler: --teile N oder --laenge SEKUNDEN angeben.": "Erreur : indiquer --teile N ou --laenge SECONDES.",
        "Fehler: %.1f Minuten pro Teil sind zu lang (Grenze %d Minuten).": "Erreur : %.1f minutes par partie, c'est trop long (limite %d minutes).",
        "Fehler: '%s' gibt es nicht.": "Erreur : '%s' n'existe pas.",
        "Fehler: '%s' ist keine lesbare Audiodatei.%s": "Erreur : '%s' n'est pas un fichier audio lisible.%s",
        "Fehler: '%s' liegt schon vor.": "Erreur : '%s' existe déjà.",
        "Fehler: Die Datei ließ sich nicht kürzen.": "Erreur : le fichier n'a pas pu être raccourci.",
        "Fehler: Die Umwandlung hat keine hörbare Aufnahme\n  ergeben (nur Stille). Datei prüfen: %s": "Erreur : la conversion n'a produit aucun son audible\n  (silence seulement). Vérifier le fichier : %s",
        "Fehler: Patch%d gibt es nicht.": "Erreur : le patch %d n'existe pas.",
        "Fehler: Die Aufnahme ließ sich nicht ausblenden (kein 16-Bit-WAV?).": "Erreur : l'enregistrement n'a pas pu être fondu (pas un WAV 16 bits ?).",
        "Fehler: '%s' ist nicht eingelegt.": "Erreur : '%s' n'est pas insérée.",
        "Fehler beim Auswerfen: %s": "Erreur lors de l'éjection : %s",
        "Fehler: Patch%d hat keine WAV.": "Erreur : le patch %d n'a pas de WAV.",
        "Fehler: Sicherungsordner '%s' gibt es nicht.": "Erreur : le dossier de sauvegarde '%s' n'existe pas.",
        "Fehler: in '%s' liegen keine Patch-Ordner.": "Erreur : '%s' ne contient aucun dossier de patch.",
        "Fehler: keine Sicherung mit einer Aufnahme für Patch%d in '%s'.\n  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade.": "Erreur : aucune sauvegarde avec un enregistrement pour le patch %d dans '%s'.\n  Les sauvegardes de fondu s'appellent Sicherung_<date>_vor_fade.",
        "Fehler: Platz %d ist belegt (%s).\n  Zum Tauschen:  tauschen --patch %d --mit %d": "Erreur : l'emplacement %d est occupé (%s).\n  Pour échanger :  tauschen --patch %d --mit %d",
        "Fehler: Nummern müssen zwischen 1 und %d liegen.": "Erreur : les numéros doivent être entre 1 et %d.",
        "Fehler: '%s' ist ein Ordner, keine Datei.": "Erreur : '%s' est un dossier, pas un fichier.",
        "Fehler: '%s' ist kein Pegelprotokoll (keine lesbare Textdatei).": "Erreur : '%s' n'est pas un protocole de niveau (pas de fichier texte lisible).",
        "Fehler: '%s' darf nicht gelesen werden.": "Erreur : '%s' ne doit pas être lu.",
        "Fehler: '%s' lässt sich nicht lesen: %s": "Erreur : '%s' est illisible : %s",
        "Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n  ffmpeg installieren.": "Erreur lors de la conversion. Le fichier a %d Hz / %d canal(aux) ;\n  44100 Hz et 2 canaux sont nécessaires. Le convertir d'abord ou\n  installer ffmpeg.",
        "Fehler: '%s' lässt sich nicht schreiben.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Erreur : '%s' ne peut pas être écrit.\n  Choisir un autre dossier :  --sicherung <chemin>",
        "Fehler: Patch%d hat keine phrase.wav.": "Erreur : le patch %d n'a pas de phrase.wav.",
        "Fehler: Der Pegel ließ sich nicht ändern.": "Erreur : le niveau n'a pas pu être modifié.",
        "Fehler: in '%s' darf nicht geschrieben werden.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Erreur : '%s' ne doit pas être écrit.\n  Choisir un autre dossier :  --sicherung <chemin>",
        "Fehler: Teil %d ließ sich nicht schneiden.": "Erreur : la partie %d n'a pas pu être coupée.",
        "Fehler: Die Datei ist %.1f Minuten lang.\n  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n  nicht erkannt - und können dabei verschwinden.\n\n  So geht es weiter:\n    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n                         12 ms ausgeblendet, damit es nicht knackt)\n    --laenge 480         auf 8:00 kürzen\n    --trotzdem           unverändert schreiben (zum Ausprobieren)": "Erreur : le fichier dure %.1f minutes.\n  Le JamMan Solo XT accepte au plus %d minutes par boucle\n  (manuel, page 60 : « Max. Individual Loop Time: 10 minutes »).\n  Les boucles plus longues apparaissent sur la carte mais la pédale\n  ne les reconnaît pas - et elles peuvent disparaître.\n\n  Solutions :\n    --kuerzen            réduire à 9:59 (coupe nette à la fin,\n                         12 ms fondues pour éviter le clic)\n    --laenge 480         réduire à 8:00\n    --trotzdem           écrire inchangé (pour essayer)",
        "Fehler: --wav fehlt.": "Erreur : --wav manque.",
        "Fehler: --patch fehlt.": "Erreur : --patch manque.",
        "Fehler: --protokoll fehlt.": "Erreur : --protokoll manque.",
        "Fehler: --patch fehlt (oder --alle angeben).": "Erreur : --patch manque (ou donner --alle).",
        "Fehler: --patch und --auf fehlen.": "Erreur : --patch et --auf manquent.",
        "Fehler: --patch und --mit fehlen.": "Erreur : --patch et --mit manquent.",
        "Fehler: --patch und --nach fehlen.": "Erreur : --patch et --nach manquent.",
        "  Hinweis: Pegelprotokoll konnte nicht geschrieben werden.": "    Remarque : le protocole de niveau n'a pas pu être écrit.",
        "  Protokoll: %s": "    Protocole : %s",
        "  %d Einträge": "    %d entrées",
        "    %-10s %-24s Faktor %.4f → 1/%.4f": "      %-10s %-24s facteur %.4f → 1/%.4f",
        "  Trockenlauf — nichts geändert. Mit --schreiben ausführen.": "    Simulation — rien modifié. Lancer avec --schreiben pour écrire.",
        "    %-10s %+6.1f → %+6.1f dB": "      %-10s %+6.1f → %+6.1f dB",
        "  Zurückgerechnet: %d Patches. Sicherung davor: %s": "    Recalculé : %d patches. Sauvegarde avant : %s",
        "  Pegel:        %.1f dB → %.1f dB Spitze": "    Niveau :      %.1f dB → %.1f dB de crête",
        "                Die Anhebung wurde begrenzt, damit nichts": "                L'augmentation a été limitée pour que rien",
        "                über 0 dBFS geht (sonst würde übersteuert).": "                ne dépasse 0 dBFS (sinon saturation).",
        "                Achtung: %d Werte liegen an der Grenze.": "                Attention : %d valeurs sont à la limite.",
        "  Hinweis: unbekanntes WAV-Format 0x%04X - nicht angefasst.": "    Remarque : format WAV inconnu 0x%04X — non modifié.",
        "Fehler: '%s' ist kein Ordner. Ist die Karte eingelegt?": "  Erreur : '%s' n'est pas un dossier. La carte est-elle insérée ?",
        "Fehler: auf '%s' liegt kein Ordner '%s' oder '%s'. Das ist keine JamMan-Karte.": "  Erreur : '%s' ne contient pas de dossier '%s' ni '%s'. Ce n'est pas une carte JamMan.",
        "  %d Apple-Dateien entfernt (._* und .DS_Store).": "    %d fichiers Apple supprimés (._* et .DS_Store).",
        "  Keine Patches auf der Karte.": "    Aucun patch sur la carte.",
        "  %-10s %-34s %8s  %s": "    %-10s %-34s %8s  %s",
        "  %-10s %-34s %8s  %s%s%s": "    %-10s %-34s %8s  %s%s%s",
        "\n  %d Patches, %s belegt.": "\n  %d patches, %s occupés.",
        "  Sicherung beim Start: Karte unverändert, nichts zu tun.": "    Sauvegarde au démarrage : carte inchangée, rien à faire.",
        "  Sicherung beim Start: %s": "    Sauvegarde au démarrage : %s",
        "    gesichert: %d Patches, %.0f MB": "      sauvegardés : %d patches, %.0f Mo",
        "    alte Sicherung entfernt: %s": "      ancienne sauvegarde supprimée : %s",
        "  Quelle: %s": "    Source : %s",
        "  Ziel:   %s": "    Cible : %s",
        "  Trockenlauf — nichts kopiert. Mit --schreiben ausführen.": "    Simulation — rien copié. Lancer avec --schreiben pour écrire.",
        "  Gesichert: %d Patches, %.0f MB.": "    Sauvegardé : %d patches, %.0f Mo.",
        "  Datei:  %s": "    Fichier : %s",
        "  Dauer:  %s": "    Durée : %s",
        "  Teile:  %d × etwa %s": "    Parties : %d × environ %s",
        "  Suche musikalisch brauchbare Schnittstellen …": "    Recherche de points de coupe utilisables …",
        "  Hinweis: keine brauchbaren Pausen gefunden (%s) -": "    Remarque : aucune pause utilisable (%s) —",
        "           es wird gleichmäßig geschnitten.": "             coupe régulière à la place.",
        "  Pause an der Naht wird herausgenommen": "    La pause à la jonction est retirée",
        "    Teil %d: %7.1f – %7.1f s  (%s)": "      Partie %d : %7.1f – %7.1f s  (%s)",
        "  Hinweis: Teil %d wäre %.1f min - er wird auf 9:59 gekürzt.": "    Remarque : la partie %d ferait %.1f min — elle est réduite à 9:59.",
        "    Teil %d: %s – %s": "      Partie %d : %s – %s",
        "  Trockenlauf — nichts angelegt. Mit --schreiben ausführen.": "    Simulation — rien créé. Lancer avec --schreiben pour écrire.",
        "  Quelle:       %s": "    Source :      %s",
        "  Dauer:        %s": "    Durée :       %s",
        "  Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s": "    Format :      %d Hz / %d bit / %d canal(aux)%s",
        "  Stopp:        %s": "    Arrêt :       %s",
        "  Rhythmus:     %s": "    Rythme :      %s",
        "  Wiedergabe:   %s": "    Lecture :     %s",
        "  Fade:         letzte %.1f s werden ausgeblendet": "    Fondu :       les %.1f dernières s sont fondues",
        "  Pegel:        auf -1 dB Spitze normieren": "    Niveau :      normaliser à -1 dB de crête",
        "  Pegel:        %+.1f dB": "    Niveau :      %+.1f dB",
        "  Neuer Patch:  Patch%s  (Name: %s)": "    Nouveau patch : Patch%s  (nom : %s)",
        "  Vorlage:      %s": "    Modèle :      %s",
        "  Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.": "    Simulation — rien écrit. Lancer avec --schreiben pour écrire.",
        "  Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)": "    Raccourcir :  coupé à %s (coupe nette, 12 ms fondues)",
        "  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben": "    En-tête :     en-tête WAV réécrit en PCM simple",
        "  Fertig: %s angelegt.": "    Terminé : %s créé.",
        "  Karte auswerfen, in den JamMan stecken — der Patch erscheint als Patch%s.": "    Éjectez la carte, mettez-la dans le JamMan — le patch apparaît comme Patch%s.",
        "  Patch:        %s  (%s)": "    Patch :       %s  (%s)",
        "  Stopp:        %s (%s)": "    Arrêt :       %s (%s)",
        "  Rhythmus:     %s (%s)": "    Rythme :      %s (%s)",
        "  Wiedergabe:   %s (IsLoop %s)": "    Lecture :     %s (IsLoop %s)",
        "  Name:         %s": "    Nom :         %s",
        "  Fade:         letzte %.1f s der Aufnahme ausblenden": "    Fondu :       les %.1f dernières s de l'enregistrement sont fondues",
        "                (verändert die Audiodatei; vorher wird gesichert)": "                (modifie le fichier audio ; une sauvegarde est faite avant)",
        "  Geändert. Sicherung: %s": "    Modifié. Sauvegarde : %s",
        "  %s Sie kann jetzt abgezogen werden.": "    %s Elle peut être retirée.",
        "  Schreibweise des Geräts: Patch%0*d": "    Orthographe de l'appareil : Patch%0*d",
        "  Alle Ordner heißen schon so. Nichts zu tun.": "    Tous les dossiers sont déjà ainsi. Rien à faire.",
        "  Zu ändern: %d": "    À modifier : %d",
        "    %-12s ->  %s": "      %-12s ->  %s",
        "    %-12s FEHLER: %s": "      %-12s ERREUR : %s",
        "  Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.": "    Terminé : %d dossiers renommés, %d erreurs, %d patches lisibles.",
        "  Zuordnung liegt in: %s": "    La correspondance est dans : %s",
        "  %d Apple-Dateien auf der Karte. Mit --schreiben entfernen.": "    %d fichiers Apple sur la carte. Les retirer avec --schreiben.",
        "  Fertig.": "    Terminé.",
        "  Spielt \"%s\" (%s) … (Strg+C hält an)": "    Lecture de « %s » (%s) … (Ctrl+C arrête)",
        "  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB": "    Cible : %.1f dB de sonie (RMS), crête au plus %.1f dB",
        "  Patches: %d": "    Patches : %d",
        "  %-10s %-24s %8s": "    %-10s %-24s %8s",
        "  %-10s %-24s %+7.1f dB": "    %-10s %-24s %+7.1f dB",
        "  Sicherung: %s": "    Sauvegarde : %s",
        "  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s": "    %-10s %-24s %+6.1f → %+6.1f dB  (crête %+.1f dB)%s",
        "  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.": "    Terminé : %d patches normalisés, dont %d écrêtés en crête.",
        "  Patches in der Sicherung: %d": "    Patches dans la sauvegarde : %d",
        "  Patches auf der Karte:    %d": "    Patches sur la carte :    %d",
        "  Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:": "    Remarque : ceux-ci n'existent pas sur la carte et seront créés :",
        "           %s": "             %s",
        "  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.": "    Simulation — rien restauré. Lancer avec --schreiben pour écrire.",
        "  Zurückgeholt. Der Zustand davor liegt in: %s": "    Restauré. L'état précédent est dans : %s",
        "  Patch:      %s  (%s)": "    Patch :     %s  (%s)",
        "  Sicherung:  %s": "    Sauvegarde : %s",
        "    %-22s %8.2f MB": "      %-22s %8.2f Mo",
        "  %s  (%s)": "    %s  (%s)",
        "  →  %s": "    →  %s",
        "  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.": "    Simulation — rien renommé. Lancer avec --schreiben pour écrire.",
        "  Fertig. Sicherung: %s": "    Terminé. Sauvegarde : %s",
        "  Nichts zu tun.": "    Rien à faire.",
        "  Achtung: %s und %s haben unterschiedliche Stellenzahl.": "    Attention : %s et %s n'ont pas le même nombre de chiffres.",
        "           Der JamMan sortiert nach Namen - der Patch landet dann": "             Le JamMan trie par nom — le patch atterrira",
        "           auf dem Platz, den sein neuer Name angibt.": "             sur l'emplacement qu'indique son nouveau nom.",
        "  Tauschen:": "    Échange :",
        "    Platz %-4s %-24s  →  Platz %s": "      place %-4s %-24s  →  place %s",
        "  Trockenlauf — nichts getauscht. Mit --schreiben ausführen.": "    Simulation — rien échangé. Lancer avec --schreiben pour écrire.",
        "  Getauscht. Sicherung: %s": "    Échangé. Sauvegarde : %s",
        "  Wird gelöscht: %s  (%s)": "    Sera supprimé : %s  (%s)",
        "  Vorher wird er gesichert nach: %s": "    Il est d'abord sauvegardé dans : %s",
        "  Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.": "    Simulation — rien supprimé. Lancer avec --schreiben pour écrire.",
        "  Gelöscht. Sicherung: %s": "    Supprimé. Sauvegarde : %s",
        "  Karte auswerfen, in den JamMan stecken — der Patch ist weg.": "    Éjectez la carte, mettez-la dans le JamMan — le patch a disparu.",
        "  Neue Reihenfolge:": "    Nouvel ordre :",
        "    Platz %d:  %-10s  %s%s": "      place %d :  %-10s  %s%s",
        "  Verschoben. Sicherung: %s": "    Déplacé. Sauvegarde : %s",
        "  JamMan Solo XT — Speicherkarte verwalten": "    JamMan Solo XT — gérer la carte mémoire",
        "  Keine Karte gefunden unter %s.": "    Aucune carte trouvée sous %s.",
        "  Karte: %s": "    Carte : %s",
        "  1  Patches auflisten": "    1  Lister les patches",
        "  2  Patch anhören": "    2  Écouter un patch",
        "  3  Datei aufspielen": "    3  Importer un fichier",
        "  4  Patch ändern (Stopp, Wiedergabe, Name)": "    4  Modifier un patch (arrêt, lecture, nom)",
        "  5  Patch verschieben": "    5  Déplacer un patch",
        "  6  Patch löschen": "    6  Supprimer un patch",
        "  7  Alles sichern": "    7  Tout sauvegarder",
        "  8  Karte auswerfen": "    8  Éjecter la carte",
        "  0  Beenden": "    0  Quitter",
        "  %s": "    %s",
        "  System:        %s %s": "    Système :      %s %s",
        "  Python:        %s": "    Python :       %s",
        "  Fassung:       %s": "    Version :      %s",
        "  Tk:            %s": "    Tk :           %s",
        "  Tk-Modul:      %s": "    Module Tk :    %s",
        "  Tk:            NICHT verfügbar — %s": "    Tk :           NON disponible — %s",
        "                 (Fenster geht mit diesem Python nicht;": "                    (la fenêtre ne marche pas avec ce Python ;",
        "                  das Textmenü geht trotzdem: jamman.py menu)": "                     le menu texte fonctionne : jamman.py menu)",
        "  Ton:           winsound (Windows-eigen)": "    Son :          winsound (intégré à Windows)",
        "  Ton:           afplay": "    Son :          afplay",
        "  Ton:           %s": "    Son :          %s",
        "  Kartenwurzeln: %s": "    Racines des cartes : %s",
        "  Einstellungen: %s": "    Réglages :     %s",
        "  ffmpeg:        %s": "    ffmpeg :       %s",
        "  Schrift:       %s": "    Police :       %s",
        "  Pythons auf diesem Rechner:": "    Pythons sur cette machine :",
        "    %-62s %s": "      %-62s %s",
        "  Geprüfte Orte:": "    Endroits vérifiés :",
        "  Laufwerke mit JamManSoloXT:": "    Lecteurs avec JamManSoloXT :",
        "    %-24s %3d Patches  %s": "      %-24s %3d patches  %s",
        "  gewählt:       %s": "    choisi :       %s",
        "  Patches:       %d": "    Patches :      %d",
        "  ACHTUNG: das Dateisystem liefert Lesefehler.": "    ATTENTION : le système de fichiers signale des erreurs de lecture.",
        "  Betroffene Einträge (erste drei): %s": "    Entrées concernées (trois premières) : %s",
        "  Inhalt von %s:": "    Contenu de %s :",
        "    %-24r %s, %s": "      %-24r %s, %s",
        "    nicht lesbar: %s": "      illisible : %s",
        "    %-10s %s": "      %-10s %s",
        "  erste Zeile im Fenster:": "    première ligne dans la fenêtre :",
        "    %r": "      %r",
        "JamMan Solo XT": "  JamMan Solo XT",
        "WAV-Format": "Format WAV",
        "%d Hz / %d Bit / %d K": "%d Hz / %d bit / %d can",
        "  %-28s  (gibt es nicht)": "  %-28s  (n'existe pas)",
        "  %-28s  (nicht lesbar: %s)": "  %-28s  (illisible : %s)",
        "  %-28s  (leer)": "  %-28s  (vide)",
        "  %-28s  %s mit %s Patches": "  %-28s  %s avec %s patches",
        "kein nutzbares Tk": "aucun Tk utilisable",
        "Sprache der Ausgabe (Vorgabe: aus dem System)": "Langue de sortie (par défaut : celle du système)",
        "Laufwerk der Karte (Vorgabe: automatisch erkannt)": "Lecteur de la carte (par défaut : détecté automatiquement)",
        "Zielordner für 'sichern'": "Dossier de destination pour « sauvegarder »",
        "Audiodatei für 'aufspielen'": "Fichier audio pour « importer »",
        "Name des Patches (Vorgabe: Dateiname)": "Nom du patch (par défaut : nom du fichier)",
        "Patch-Nummer": "Numéro du patch",
        "Zielplatz für 'verschieben'": "Emplacement cible pour « déplacer »",
        "Tauschpartner für 'tauschen'": "Partenaire d'échange pour « échanger »",
        "Zielplatz für 'platzieren'": "Emplacement cible pour « placer »",
        "Sicherungsordner für 'zuruecknehmen'": "Dossier de sauvegarde pour « restaurer »",
        "Pegelprotokoll für 'pegel-umkehren'": "Protocole de niveau pour « annuler le niveau »",
        "bei 'zuruecknehmen': alle Patches aus der Sicherung": "pour « restaurer » : tous les patches de la sauvegarde",
        "Ziellautheit für 'alle-normieren' (Vorgabe -18 dB)": "Volume cible pour « tout normaliser » (par défaut -18 dB)",
        "Spitzengrenze für 'alle-normieren' (Vorgabe -1 dB)": "Limite de crête pour « tout normaliser » (par défaut -1 dB)",
        "zu lange Datei auf 9:59 kürzen": "raccourcir un fichier trop long à 9:59",
        "beim Teilen die Pause an der Naht stehen lassen": "lors du découpage, garder la pause à la jonction",
        "beim Teilen gleichmäßig schneiden statt an Pausen": "lors du découpage, couper régulièrement au lieu des pauses",
        "Datei in N gleich lange Patches aufteilen": "diviser le fichier en N patches de même longueur",
        "zu lange Datei auf diese Länge kürzen": "raccourcir le fichier trop long à cette longueur",
        "auch Loops über 10 Minuten schreiben (Pedal nimmt sie nicht an)": "écrire aussi des boucles de plus de 10 minutes (la pédale les refuse)",
        "Fade am Ende der Datei einrechnen (z. B. 3)": "tenir compte du fondu en fin de fichier (p. ex. 3)",
        "Lautstärke um DB ändern (z. B. -3 oder +2)": "modifier le volume de DB (p. ex. -3 ou +2)",
        "Anhebung NICHT auf 0 dBFS begrenzen (kann übersteuern)": "ne PAS limiter l'augmentation à 0 dBFS (risque de saturation)",
        "auf -1 dB Spitzenpegel normieren": "normaliser à -1 dB de crête",
        "Stopp-Verhalten des neuen Patches (ohne Angabe: wie Vorlage)": "comportement d'arrêt du nouveau patch (sans indication : comme le modèle)",
        "Loop oder Single (IsLoop in phrase.xml; ohne Angabe: wie Vorlage)": "boucle ou single (IsLoop dans phrase.xml ; sans indication : comme le modèle)",
        "Rhythmus des neuen Patches (ohne Angabe: wie Vorlage)": "rythme du nouveau patch (sans indication : comme le modèle)",
        "beim Verschieben nur die Namen sichern (schnell, kein Kopieren)": "lors du déplacement, ne sauvegarder que les noms (rapide, sans copie)",
        "Ordner für die Sicherung vor Löschen/Verschieben": "dossier de sauvegarde avant suppression/déplacement",
        "wirklich verändern (sonst Trockenlauf)": "modifier réellement (sinon simulation)",
        "was zu tun ist (Liste unten; englische Namen gehen auch)": "ce qu'il faut faire (liste ci-dessous ; les noms anglais marchent aussi)",
        "Einstellungen: %s": "Réglages : %s",
        "Keine Karte gefunden": "Aucune carte trouvée",
        "Neue Fassung verfügbar": "Nouvelle version disponible",
        "Es gibt eine neuere Fassung: %s. Sie benutzen %s.": "Il existe une version plus récente : %s. Vous utilisez %s.",
        "Zur Download-Seite wechseln?": "Ouvrir la page de téléchargement ?",
        "Es liegt keine JamMan-Karte im Rechner.": "Il n'y a pas de carte JamMan dans cet ordinateur.",
        "Nimm die microSD aus dem Pedal und stecke sie in einen Kartenleser oder Kartenslot. Über das USB-Kabel am Pedal erscheint sie nicht als Laufwerk.": "Retirez la microSD de la pédale et insérez-la dans un lecteur de cartes ou une fente. Avec le câble USB branché sur la pédale, elle n'apparaît pas comme lecteur.",
        "Ordner selbst wählen?": "Choisir le dossier vous-même ?",
        "Ordner der Karte wählen": "Choisir le dossier de la carte",
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
        "vormerken": "réserver",
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
        'DATEIEN':
         'ARCHIVOS',
        'Funktionstest in allen fünf Sprachen':
         'autoprueba en los cinco idiomas',
        'Dauer und Einstellungen kommen aus Dateigröße und WAV-Kopf — der Ton selbst wird dafür nicht übertragen.':
         'La duración y los ajustes solo aparecen tras traerlos — por USB solo se lee el nombre.',
        'Marken außerhalb der Datei wurden verworfen.':
         'Se descartaron las marcas fuera del archivo.',
        '%d Teile: %s':
         '%d partes: %s',
        '%d Teile, Schnitt an Pausen gesucht':
         '%d partes, cortes buscados en las pausas',
        '%d Teile, gleichmäßig geschnitten':
         '%d partes, corte regular',
        'in %d Patches geteilt':
         'dividido en %d patches',
        'Marken':
         'Marcas',
        'Teilen':
         'Dividir',
        'Alle Marken löschen':
         'Borrar todas las marcas',
        'Alle Pausen als Marken':
         'Todas las pausas como marcas',
        'Pausen gefunden: %d':
         'Pausas encontradas: %d',
        'Keine Pause gefunden.':
         'Ninguna pausa encontrada.',
        'Wellenform nicht lesbar.':
         'No se puede leer la forma de onda.',
        '%s   •   Klick setzt eine Marke, Rechtsklick entfernt sie':
         '%s   •   un clic pone una marca, clic derecho la quita',
        'Schwelle %':
         'Umbral %',
        'Pause s':
         'Pausa s',
        'Fade ms':
         'Fundido ms',
        'Marken bitte als Zahlen mit Komma, z. B. 60, 120.':
         'Introduce las marcas como números separados por comas, p. ej. 60, 120.',
        'Schwelle, Pause und Fade bitte als Zahlen eintragen.':
         'Introduce umbral, pausa y fundido como números.',
        'Schwelle bitte als Zahl zwischen 1 und 99.':
         'El umbral debe ser un número entre 1 y 99.',
        'Pause bitte als Zahl zwischen 0,01 und 30 Sekunden.':
         'La pausa debe ser un número entre 0,01 y 30 segundos.',
        'Fade bitte als Zahl zwischen 0 und 50.':
         'El fundido debe ser un número entre 0 y 50.',
        'Es wird an %d gesetzten Marken geschnitten.':
         'Se corta en %d marcas.',
        'Es wird an allen %d gefundenen Pausen geschnitten.':
         'Se corta en todas las %d pausas encontradas.',
        'Schnittpunkt in Sekunden (mehrfach möglich)':
         'punto de corte en segundos (repetible)',
        'an jeder gefundenen Pause trennen':
         'cortar en cada pausa encontrada',
        'Ruhepegel für die Pausenerkennung in Prozent (Vorgabe 35)':
         'nivel de silencio para detectar pausas (por defecto 35)',
        'kürzeste Pause, an der geschnitten wird (Vorgabe 0,25 s)':
         'pausa más corta donde cortar (por defecto 0,25 s)',
        'Fade an jeder Naht in Millisekunden (Vorgabe 0)':
         'fundido en cada unión en milisegundos (por defecto 0)',
        'Ende einer Pause':
         'final de una pausa',
        'Marke':
         'marca',
        'Herausgeben':
         'Exportar',
        'Alles herausgeben …':
         'Exportarlo todo …',
        'Wohin herausgeben?':
         '¿Dónde exportar?',
        'Erst eine Karte öffnen — dann läßt sie sich herausgeben.':
         'Abre primero una tarjeta — entonces se puede exportar.',
        'Herausgegeben nach: %s':
         'Exportado a: %s',
        '%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv und LIESMICH.txt.':
         '%d pistas en WAV, %d posiciones sin sonido, además karte.csv y LIESMICH.txt.',
        'Die Karte wurde nur gelesen. Die Dateien brauchen dieses Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text.':
         'La tarjeta solo se leyó. Los archivos no necesitan esta herramienta: un WAV es un WAV y la lista es texto.',
        'Herausgabe einer JamMan-Karte':
         'Exportación de una tarjeta JamMan',
        'Datei':
         'Archivo',
        'Gerät: %s':
         'Dispositivo: %s',
        'Aufgenommen: %s':
         'Registrado: %s',
        'Werkzeug: %s':
         'Herramienta: %s',
        'In diesem Ordner liegen %d Tonspuren als gewöhnliche WAV-Dateien, benannt nach ihrem Platz und ihrem Namen in der Karte. %d Plätze hatten keinen Ton.':
         'Esta carpeta contiene %d pistas como archivos WAV normales, nombradas según su posición y su nombre en la tarjeta. %d posiciones no tenían sonido.',
        'karte.csv enthält zu jeder Spur: Platz, Name, Datei, Dauer, Stoppart, Wiedergabe, Tempo und Rhythmus. Semikolon als Trennzeichen, damit Excel und LibreOffice sie direkt öffnen.':
         'karte.csv indica por pista: posición, nombre, archivo, duración, modo de parada, reproducción, tempo y ritmo. Separado por puntos y coma para que Excel y LibreOffice lo abran directamente.',
        'Diese Dateien brauchen das Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text. Wer später ein anderes Gerät oder Programm benutzt, kann damit weiterarbeiten. Die Karte selbst wurde nur gelesen, nicht verändert.':
         'Estos archivos no necesitan la herramienta: un WAV es un WAV y la lista es texto. Quien pase después a otro dispositivo o programa puede seguir trabajando con ellos. La tarjeta solo se leyó, nunca se modificó.',
        'Herausgabe: %d WAV-Dateien gezählt, %d erwartet':
         'Exportación: %d archivos WAV contados, %d esperados',
        'Herausgabe: karte.csv fehlt':
         'Exportación: falta karte.csv',
        'Herausgabe: %d Zeilen in der Liste, %d erwartet':
         'Exportación: %d líneas en la lista, %d esperadas',
        'Herausgabe: LIESMICH.txt fehlt':
         'Exportación: falta LIESMICH.txt',
        'Nur-Lesen-Modus: „%s“ ist ein unbekanntes Gerät (%s). Der Kartenaufbau ist nicht belegt, deshalb wird nichts geschrieben. Lesen ist erlaubt (liste). Mit „jamman.py struktur <karte>“ entsteht ein Bericht, den Sie weitergeben können.':
         'Modo de solo lectura: «%s» es un dispositivo desconocido (%s). La estructura de la tarjeta no está verificada, por eso no se escribe nada. La lectura está permitida (liste). «jamman.py struktur <tarjeta>» crea un informe que puedes enviar.',
        'Speicherkarte des %s':
         'Tarjeta de memoria del %s',
        'Struktur melden …':
         'Informar de la estructura …',
        'Strukturbericht':
         'Informe de estructura',
        'Der Bericht ließ sich nicht schreiben: %s':
         'No se pudo escribir el informe: %s',
        'Nur-Lesen-Modus: unbekanntes Gerät „%s“. Die Karte wird gelesen; geschrieben wird nichts, weil der Aufbau nicht belegt ist. Mit „Struktur melden …“ können Sie ihn belegen.':
         'Modo de solo lectura: dispositivo desconocido «%s». La tarjeta se lee; no se escribe nada porque la estructura no está verificada. «Informar de la estructura …» aporta esa prueba.',
        'Bericht geschrieben: %s':
         'Informe escrito: %s',
        'Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.':
         'Contiene: nombres de carpetas y los archivos XML, sin audio y sin tus textos (nombres de patches sustituidos por …). %d archivos XML.',
        'Bitte schicken Sie diese Datei an mich oder hängen Sie sie an ein GitHub-Issue — dann kann ich den Aufbau prüfen und das Gerät freigeben.':
         'Envíame este archivo o adjúntalo a un issue de GitHub — así podré revisar la estructura y aprobar el dispositivo.',
        'Spalten (%s): %s':
         'Columnas (%s): %s',
        'Spalten (%s): Aufbau unvollständig':
         'Columnas (%s): estructura incompleta',
        'Länge':
         'Duración',
        'fade':
         'fundido',
        'loop':
         'bucle',
        'single':
         'único',
        "Fehler: auf '%s' liegt kein unterstützter JamMan-Geräteordner (%s). Das ist keine JamMan-Karte.":
         "  Error: '%s' no contiene una carpeta de dispositivo JamMan compatible (%s). No es una tarjeta JamMan.",
        'beim Start automatisch nach Updates sehen':
         'buscar actualizaciones automáticamente al iniciar',
        'nach jedem Update automatisch prüfen':
         'comprobar automáticamente tras cada actualización',
        'Aktualisierung':
         'Actualización',
        'Selbsttest nach Update':
         'Prueba tras actualizar',
        'Fenster zu schmal für seinen Inhalt: %d von %d Pixel — bitte breiter ziehen':
         'Ventana demasiado estrecha para su contenido: %d de %d píxeles — amplíala',
        'abgeschnittene Beschriftung: %s':
         'etiqueta cortada: %s',
        'Die letzte Sicherung stammt aus dem anderen Speicher. Bitte oben umschalten und es noch einmal versuchen.':
         'La última copia de seguridad es del otro almacenamiento. Cambia arriba e inténtalo de nuevo.',
        'deutscher Text in der Anzeige: %s':
         'texto alemán en la pantalla: %s',
        'sofort':
         'inmediatamente',
        '%s   •   %s   •   Klick in die Spur springt dorthin':
         '%s   •   %s   •   haz clic en la pista para saltar allí',
        'Per E-Mail senden':
         'Enviar por correo',
        'Mail im Mailprogramm vorbereitet — abschicken mußt du selbst.':
         'Correo preparado en tu programa de correo — envíalo tú mismo.',
        'Mailprogramm ließ sich nicht öffnen.':
         'No se pudo abrir el programa de correo.',
        'Hier liegen einmal die Zusatzstarter und die Textdateien.\nDer Selbsttest im Werkzeug schreibt seinen Bericht dorthin, wo das Programm liegt: .jamman/.\n':
         'Esta carpeta contiene los iniciadores adicionales y los archivos de texto.\nLa prueba escribe su informe donde está el programa: .jamman/.\n',
        '  Ordner aufgeräumt — das Werkzeug startet neu …':
         '  Carpetas ordenadas — la herramienta se reinicia …',
        'Diese Fassung liegt in einer anderen Ordneraufteilung. Bitte einmal von Hand herunterladen:\n%s':
         'Esta versión usa otra estructura de carpetas. Descárgala una vez a mano:\n%s',
        'Diese Fassung des Werkzeugs kann sich nicht selbst aktualisieren (neue Ordneraufteilung). Bitte einmal von Hand herunterladen:\n%s':
         'Esta versión de la herramienta no puede actualizarse sola (nueva estructura de carpetas). Descárgala una vez a mano:\n%s',
        'Als Issue vorbereiten':
         'Preparar como incidencia',
        'Issue im Browser vorbereitet — abschicken mußt du selbst.':
         'Incidencia preparada en el navegador — envíala tú mismo.',
        'Browser ließ sich nicht öffnen.':
         'No se pudo abrir el navegador.',
        'Nichts wird gesendet, solange du nicht selbst auf GitHub auf Absenden drückst.':
         'No se envía nada mientras no pulses tú mismo Enviar en GitHub.',
        'Selbsttest':
         'Autocomprobación',
        'Selbsttest …':
         'Autocomprobación …',
        'Bericht des Selbsttests':
         'Informe de la prueba',
        'Kopieren':
         'Copiar',
        'Kopieren ging nicht.':
         'No se pudo copiar.',
        'In die Zwischenablage kopiert.':
         'Copiado al portapapeles.',
        'Das Werkzeug prüft sich selbst: es legt eine Testkarte an, drückt jeden Knopf und spielt die Dateibefehle durch.\n\nDabei gehen kurz Fenster auf und zu. Das Pedal bleibt unberührt, es wird nichts an deinen Karten geändert.\n\nDauert ein paar Sekunden. Fortfahren?':
         'La herramienta se prueba a sí misma: crea una tarjeta de prueba, pulsa cada botón y ejecuta los comandos de archivo.\n\nSe abren y cierran ventanas brevemente. El pedal no se toca, no se cambia nada en tus tarjetas.\n\nTarda unos segundos. ¿Continuar?',
        '  [Hinweis] Kein Fenster geprüft (%s): dieses Python hat Tk 8.5 — es lief das Textmenü.':
         '  [nota] no se comprobó ventana (%s): este Python tiene Tk 8.5 — se usó el menú de texto.',
        '  Funktionstest des JamMan Card Tool':
         '  Prueba de funcionamiento del JamMan Card Tool',
        '  System:  %s':
         '  Sistema: %s',
        '  Python:  %s':
         '  Python: %s',
        '  Fassung: %s':
         '  Versión: %s',
        '  Testkarte: %s':
         '  Tarjeta de prueba: %s',
        '  [ok]   Befehl %s':
         '  [ok]   comando %s',
        '  [ok]   Befehl %s meldet: %s':
         '  [ok]   comando %s informa: %s',
        '  [FEHL] Befehl %s: %s':
         '  [FALLO] comando %s: %s',
        '  [Hinweis] Ohne Tk kein Fenstertest: %s':
         '  [nota] sin Tk no hay prueba de ventana: %s',
        '  %d Befund(e) — bitte mitschicken (stehen auch in %s).':
         '  %d hallazgo(s) — por favor envíalos (también están en %s).',
        '  Ergebnis: keine Befunde.':
         '  Resultado: sin hallazgos.',
        '  [FEHL] Fenster (%s): %s':
         '  [FALLO] ventana (%s): %s',
        '  [ok]   Fenster (%s): %d Knöpfe, keine Ausnahme':
         '  [ok]   ventana (%s): %d botones, sin excepción',
        '%d Balkenfenster blieben offen':
         '%d ventanas de progreso quedaron abiertas',
        'Fenster':
         'Ventana',
        'Abgebrochen — keine Datei geschrieben.':
         'Cancelado — no se escribió ningún archivo.',
        'Es läuft noch eine Arbeit. Abbrechen und das Fenster schließen?\n\nDie unfertige Datei wird gelöscht.':
         'Todavía hay una tarea en marcha. ¿Cancelarla y cerrar la ventana?\n\nEl archivo inacabado se borrará.',
        '%d von %d Patches gelesen':
         '%d de %d parches leídos',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe aus dieser Sicherung zurückholen?\n\n  %s\n\nDie Aufnahme bleibt, wie sie ist. Der jetzige Stand wird vorher gesichert.':
         'Parche %d: ¿recuperar nombre, modo de parada, ritmo y reproducción de esta copia?\n\n  %s\n\nLa grabación queda como está. El estado actual se guarda antes.',
        'Patch%d aus dieser Sicherung wiederherstellen?\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen. Der jetzige Stand wird vorher gesichert.':
         '¿Restaurar el parche %d desde esta copia?\n\n  %s\n\nAsí se puede deshacer un fundido, por ejemplo. El estado actual se guarda antes.',
        'Einstellungen schreiben …':
         'escribir ajustes …',
        'Aufnahme bearbeiten …':
         'procesar la grabación …',
        'fertig':
         'listo',
        'einen Moment …':
         'un momento …',
        'Sicherung anlegen …':
         'crear copia …',
        'Liste neu lesen …':
         'releer la lista …',
        '  Keine Eingabe möglich — Ende.':
         '  No es posible introducir datos — fin.',
        'Sicherungen löschen':
         'Borrar copias',
        '%d von %d gelöscht':
         '%d de %d borrados',
        '%d von %d gelöscht: %s':
         '%d de %d borrados: %s',
        'Alle normieren':
         'Normalizar todo',
        '%d von %d Patches: %s':
         '%d de %d parches: %s',
        'Sicherung anlegen':
         'Crear copia',
        '%d von %d Dateien · %.0f MB':
         '%d de %d archivos · %.0f MB',
        'Doppelklick spielt · Ziehen ordnet um · ⌘ zwei markieren, dann Tauschen · Esc hält an · ⌘O Karte, ⌘S Sicherungen, ⌘L Lücken':
         'Doble clic reproduce · arrastrar reordena · ⌘ marca dos, luego Intercambiar · Esc detiene · ⌘O tarjeta, ⌘S copias, ⌘L huecos',
        'Setlist-Dateien':
         'Archivos de setlist',
        'Anpassen':
         'Ajustar',
        'Erst eine Datei markieren.':
         'Marca primero un archivo.',
        '%d Datei(en) angepasst, %d Zeile(n) umgeschrieben.':
         '%d archivo(s) ajustado(s), %d línea(s) reescrita(s).',
        'In diesem Ordner liegen keine Setlist-Dateien:\n%s\n\nBitte einen anderen Ordner wählen.':
         'No hay archivos de setlist en esta carpeta:\n%s\n\nElige otra carpeta.',
        'Alle sind markiert — einzelne mit Klick abwählen.':
         'Todos están marcados — haz clic para desmarcar alguno.',
        'betrifft Platz: %s':
         'afecta a la plaza: %s',
        'Pegeländerung zurücknehmen':
         'Deshacer cambio de nivel',
        'Es gibt noch keine Pegelprotokolle.':
         'Todavía no hay registros de nivel.',
        'Pegelprotokolle':
         'Registros de nivel',
        'Welche Änderung soll zurückgenommen werden?':
         '¿Qué cambio debe deshacerse?',
        'Erst ein Protokoll markieren.':
         'Marca primero un registro.',
        'Im Protokoll stehen keine Einträge.':
         'El registro no tiene entradas.',
        'Ein Patch (Platz %s)':
         'Un parche (plaza %s)',
        '%d Patches':
         '%d parches',
        '%s aus diesem Protokoll werden umgekehrt:\n\n%s\n\nNur diese Patches werden vorher gesichert. Fortfahren?':
         '%s de este registro se invierten:\n\n%s\n\nSólo esos parches se guardan antes. ¿Continuar?',
        'Pegeländerung zurücknehmen …':
         'Deshacer cambio de nivel …',
        '  Trockenlauf — nichts geändert.':
         '  Prueba en seco — nada cambiado.',
        '  Jetziger Stand gesichert: %s':
         '  Estado actual guardado: %s',
        '  Einstellungen von Patch%d zurückgeholt.':
         '  Ajustes del parche %d restaurados.',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe kommen aus dieser Sicherung zurück:\n\n  %s\n\nDie Aufnahme bleibt unberührt — sie war in dieser Sicherung nicht enthalten.\nDer jetzige Stand wird vorher gesichert.':
         'Parche %d: nombre, modo de parada, ritmo y reproducción vuelven de esta copia:\n\n  %s\n\nLa grabación no se toca — no formaba parte de esta copia.\nEl estado actual se guarda antes.',
        'Patch%d wird aus dieser Sicherung wiederhergestellt:\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen.\nDer jetzige Stand wird vorher gesichert.':
         'El parche %d se restaura desde esta copia:\n\n  %s\n\nAsí se puede deshacer un fundido, por ejemplo.\nEl estado actual se guarda antes.',
        'Patch%d zurückgeholt':
         'Parche %d restaurado',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nPatches, die schon passen, bleiben unangetastet. Nur die\nübrigen werden vorher gesichert und neu geschrieben.\n\nFortfahren?':
         'Todos los parches se llevan al mismo volumen:\n\n  objetivo −18 dB de sonoridad, pico como máximo −1 dB\n\nLos parches que ya encajan quedan sin tocar. Sólo los demás se\nguardan y se reescriben.\n\n¿Continuar?',
        '  Nichts zu tun: alle Patches liegen bereits auf der Ziel-Lautheit.':
         '  Nada que hacer: todos los parches ya están en el nivel objetivo.',
        '  Ein Patch war schon auf Ziel und blieb unverändert.':
         '  Un parche ya estaba en el objetivo y quedó sin tocar.',
        '  Schon auf Ziel: %d von %d Patches — unverändert gelassen.':
         '  Ya en el objetivo: %d de %d parches — sin tocar.',
        '  %d Patches waren schon auf Ziel und blieben unverändert.':
         '  %d parches ya estaban en el objetivo y quedaron sin tocar.',
        'Blättern':
         'Navegar',
        'Normierung zurücknehmen …':
         'Deshacer normalización …',
        'auf −1 dB Spitze normalisieren':
         'normalizar a −1 dB de pico',
        'Anhebung höchstens auf 0 dB':
         'limitar el aumento a 0 dB como máximo',
        'Sicherungen verwalten …':
         'Gestionar copias …',
        'Lücken bei den Plätzen %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         'Huecos en las plazas %s%s — «Cerrar los huecos» adelanta las plazas siguientes.',
        'Zurücknehmen (letzter Schritt)':
         'Deshacer (último paso)',
        'Zurücknehmen':
         'Deshacer',
        'ältere markieren':
         'marcar las más antiguas',
        'Anderer Ordner …':
         'Otra carpeta …',
        'Neue Sicherung anlegen (alles sichern)':
         'Crear una copia nueva (todo)',
        'Sicherungen …':
         'Copias …',
        'Ordner mit den Sicherungen':
         'Carpeta con las copias',
        'Sammelsicherung':
         'Copia de rescate',
        'Rette':
         'Rescatando',
        'Es gibt keine unersetzlichen Patches.':
         'No hay parches irremplazables.',
        '%d Patches wurden nach %s kopiert. Die Zuordnung steht dort in Rettung.txt. Jetzt lassen sich die alten Sicherungen löschen.':
         '%d parches se copiaron a %s. La correspondencia está en Rettung.txt. Ahora se pueden borrar las copias antiguas.',
        'Nur die unersetzlichen Patches retten':
         'Rescatar sólo los parches irremplazables',
        '⟨%d Patch nur hier⟩':
         '⟨%d parche sólo aquí⟩',
        '%d markiert · %.1f MB':
         '%d marcada(s) · %.1f MB',
        'nichts markiert':
         'nada marcado',
        'Vorgabe: neueste 3 und letzte 7 Tage behalten':
         'Por defecto: conservar las 3 más recientes y los últimos 7 días',
        'Fertig. Ordner:\n%s':
         'Listo. Carpeta:\n%s',
        'Alte Sicherungen in diesem Ordner ansehen und löschen?':
         '¿Ver las copias antiguas de esta carpeta y borrar alguna?',
        'Lücke bei Platz %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         'Hueco en la plaza %s%s — «Cerrar los huecos» adelanta las plazas siguientes.',
        'Platz %s ist nicht belegt — die Liste hat eine Lücke.':
         'La plaza %s no está ocupada — la lista tiene un hueco.',
        'Lücken schließen':
         'Cerrar los huecos',
        'Patch%d → Patch%d':
         'Patch%d → Patch%d',
        '%d Patches rücken nach vorn: %s%s\n\nDie Reihenfolge bleibt, nur die Platznummern ändern sich. Die Zuordnung wird vorher gesichert. Setlist-Dateien, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d parches avanzan: %s%s\n\nEl orden no cambia, sólo los números de plaza. La correspondencia se guarda antes. Los archivos de setlist que se refieren a números de plaza ya no coincidirán. ¿Continuar?',
        'Aufgerückt: %d Patches':
         'Avanzados: %d parches',
        'Sollen vorhandene Setlist-Dateien auf die neuen Platznummern umgeschrieben werden?':
         '¿Deben reescribirse los archivos de setlist existentes con los nuevos números?',
        'Ordner mit Setlist-Dateien':
         'Carpeta con archivos de setlist',
        'Setlists angepaßt: %d Datei(en)':
         'Setlists ajustadas: %d archivo(s)',
        'Fehler: Platz %d ist belegt (%s).':
         'Error: la plaza %d está ocupada (%s).',
        'Beenden':
         'Salir',
        'Dem Python fehlen die Zertifikate. Zwei Wege: im Python-Ordner „Install Certificates.command“ ausführen — oder „pip install certifi“ — oder die Datei von der Release-Seite von Hand laden.':
         'A Python le faltan los certificados. Dos caminos: ejecutar «Install Certificates.command» en la carpeta de Python, o «pip install certifi» — o descargar el archivo desde la página de la versión.',
        'Achtung: das Zertifikat ließ sich nicht prüfen — die Datei wurde ohne Prüfung geladen.':
         'Atención: no se pudo verificar el certificado — el archivo se descargó sin comprobación.',
        'Die Datei ist unvollständig: %d statt %d Bytes.':
         'El archivo está incompleto: %d en lugar de %d bytes.',
        'Neue Fassung %s wird geholt …':
         'Obteniendo la versión %s …',
        'Fassung %s ist installiert. Bitte das Fenster schließen und neu starten.':
         'La versión %s está instalada. Cierra la ventana y vuelve a iniciar.',
        'Update beim Start':
         'Actualizar al inicio',
        'Die Karte ist eingebunden, aber das Dateisystem liefert\nLesefehler:\n\n%s\n\nDas liegt an der Karte oder am Einhängepunkt, nicht am\nWerkzeug.\n\nErste Schritte:\n  - Karte im JamMan einschalten (das Pedal prüft sie)\n  - dmesg | tail -20\n  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n    bzw. sudo exfatfsck <gerät>':
         'La tarjeta está montada, pero el sistema de archivos informa errores de lectura:\n\n%s\n\nEs la tarjeta o el punto de montaje, no la herramienta.\n\nPrimeros pasos:\n  - poner la tarjeta en el JamMan y encenderlo (el pedal la comprueba)\n  - dmesg | tail -20\n  - desmontar y comprobar: sudo fsck.vfat -n <dispositivo>\n    o sudo exfatfsck <dispositivo>',
        'kein Pedal':
         'ningún pedal',
        'Beschreibung …':
         'Descripción …',
        'Rückwärts: ohne ffmpeg keine Hörprobe — das Pedal spielt es trotzdem rückwärts.':
         'Inverso: sin ffmpeg no hay escucha previa; el pedal lo reproduce al revés igualmente.',
        'Tempo':
         'Tempo',
        'Suchen …':
         'Buscar …',
        'Text im Namen (leer lassen = alles zeigen):':
         'Texto en el nombre (vacío = mostrar todo):',
        'Erst eine Karte oder eine geholte Kopie öffnen — dann läßt sich suchen.':
         'Abre primero una tarjeta o una copia obtenida — entonces podrás buscar.',
        'Suchen':
         'Buscar',
        'Suche fehlgeschlagen: %s':
         'Búsqueda fallida: %s',
        'Suchen:':
         'Buscar:',
        'Karte:':
         'Tarjeta:',
        'Meldungen stehen im Fenster; Fehler zusätzlich in jamman-fehler.log.':
         'Los mensajes aparecen en la ventana; los errores también en jamman-fehler.log.',
        'Firmware-Dateien jetzt ins Pedal schreiben? Ein Abbruch mittendrin kann das Pedal unbrauchbar machen. [ja/nein] ':
         '¿Escribir ahora los archivos de firmware en el pedal? Una interrupción puede dejarlo inservible. [sí/no] ',
        'Zielordner für die Firmware':
         'Carpeta de destino del firmware',
        'Ordner mit der geholten Firmware':
         'Carpeta del firmware obtenido',
        'In diesem Ordner liegt keine Firmware (usr/JamManUI, usr/loopengine …).':
         'Esta carpeta no contiene firmware (usr/JamManUI, usr/loopengine …).',
        'Firmware übernehmen':
         'Aplicar el firmware',
        'Firmware übernehmen …':
         'Aplicar el firmware …',
        'Firmware dieses Pedals holen …':
         'Traer el firmware de este pedal …',
        '%s kam unvollständig (%d von %d Bytes).':
         '%s llegó incompleto (%d de %d bytes).',
        '%d Firmware-Dateien werden jetzt in das Pedal geschrieben. Vorher wird der jetzige Stand gesichert.\n\nEin Abbruch mittendrin kann das Pedal unbrauchbar machen — es gibt keinen Weg zurück.\n\nWirklich schreiben?':
         '%d archivos de firmware se escribirán ahora en el pedal. Antes se guarda el estado actual.\n\nUna interrupción puede dejar el pedal inservible: no hay vuelta atrás.\n\n¿Escribir de verdad?',
        'Sichere den jetzigen Stand …':
         'Guardando el estado actual …',
        'Gesichert: %d Dateien nach %s':
         'Guardado: %d archivos en %s',
        'Firmware geholt: %d Dateien':
         'Firmware obtenido: %d archivos',
        'Firmware geholt:\n%s\n%d Dateien':
         'Firmware obtenido:\n%s\n%d archivos',
        'Fertig: %d Dateien geschrieben':
         'Listo: %d archivos escritos',
        'Nicht bestätigt: %d':
         'Sin confirmar: %d',
        'Firmware geschrieben: %d Dateien\nSicherung: %s\nJetzt das Pedal aus- und wieder einschalten.':
         'Firmware escrito: %d archivos\nCopia: %s\nApaga y vuelve a encender el pedal.',
        'Schreibe':
         'Escribiendo',
        'Hole':
         'Obteniendo',
        'JamList aus JamManager XT holen …':
         'Traer JamList de JamManager XT …',
        'Setlist als JamList ablegen …':
         'Guardar setlist como JamList …',
        'Setlist speichern':
         'Guardar setlist',
        'Bibliothek von JamManager XT':
         'Biblioteca de JamManager XT',
        'JamList wählen':
         'Elegir una JamList',
        'In dieser Bibliothek liegt keine JamList.':
         'Esta biblioteca no contiene ninguna JamList.',
        'Geholt: %s\n%d Plätze\n%s':
         'Obtenido: %s\n%d plazas\n%s',
        'JamList ablegen':
         'Guardar JamList',
        '%s%s%s als neue JamList eintragen? Die Bibliothek wird dabei verändert — vorher eine Kopie anlegen.':
         '¿Añadir %s%s%s como nueva JamList? La biblioteca cambiará: haz antes una copia.',
        'JamList %s angelegt (id %d, %d Plätze)':
         'JamList %s creada (id %d, %d plazas)',
        'Keine JamList mit dem Namen %s.':
         'Ninguna JamList llamada %s.',
        'belegte Plätze':
         'plazas ocupadas',
        'Geschrieben: %s (%d Plätze)':
         'Escrito: %s (%d plazas)',
        'Übernehmen':
         'Aplicar',
        'Speicherabbild':
         'Imagen de memoria',
        'Speicherabbild speichern':
         'Guardar imagen de memoria',
        'Speicherabbild aufnehmen …':
         'Tomar una imagen de memoria …',
        'Speicherabbilder vergleichen …':
         'Comparar imágenes de memoria …',
        'Speicherabbilder vergleichen':
         'Comparar imágenes de memoria',
        'Erstes Speicherabbild':
         'Primera imagen de memoria',
        'Zweites Speicherabbild':
         'Segunda imagen de memoria',
        'Nur im ersten Bild: %d':
         'Solo en la primera imagen: %d',
        'Nur im zweiten Bild: %d':
         'Solo en la segunda imagen: %d',
        'Gleicher Pfad, andere Größe: %d':
         'Misma ruta, otro tamaño: %d',
        'Die beiden Bilder sind gleich.':
         'Las dos imágenes son iguales.',
        'Erstes:':
         'Primera:',
        'Zweites:':
         'Segunda:',
        'Aufruf:':
         'Uso:',
        'Lese den Verzeichnisbaum …':
         'Leyendo el árbol de carpetas …',
        'Speicherabbild: %d Einträge':
         'Imagen de memoria: %d entradas',
        'Speicherabbild geschrieben:\n%s':
         'Imagen de memoria guardada:\n%s',
        'Speicherabbild geschrieben: %d Einträge nach %s':
         'Imagen de memoria guardada: %d entradas en %s',
        'Dateien für „speicherabbild“ und „vergleich“':
         'Archivos para «speicherabbild» y «vergleich»',
        'Sortierung':
         'Orden',
        '%d von %d Patches passen zu „%s“.':
         '%d de %d parches coinciden con «%s».',
        'Beschreibung':
         'Descripción',
        'Interpret':
         'Intérprete',
        'Genre':
         'Género',
        'Stichwörter':
         'Palabras clave',
        'Bewertung':
         'Valoración',
        'Tempo (BPM)':
         'Tempo (BPM)',
        'Die Datei %s fehlt.':
         'Falta el archivo %s.',
        'Setlists anpassen':
         'Ajustar las setlists',
        '%d Setlist-Dateien in %s an die neuen Plätze anpassen? Jede Datei wird vorher daneben gesichert.':
         '¿Ajustar %d archivos de setlist en %s a las nuevas plazas? Cada archivo se guarda antes al lado.',
        'Keine Setlist nannte einen bewegten Platz.':
         'Ninguna setlist mencionaba una plaza movida.',
        'Setlists nicht angepaßt: ':
         'Setlists no ajustadas: ',
        'Setlists angepaßt: ':
         'Setlists ajustadas: ',
        'Plätze aufrücken':
         'Cerrar los huecos',
        'Plätze aufrücken …':
         'Cerrar los huecos …',
        'Es gibt keine Lücken — die Plätze sind schon lückenlos belegt.':
         'No hay huecos: las plazas ya están contiguas.',
        '%d Patches rücken in %s nach vorn: %s%s\n\nJeder bewegte Platz wird vorher auf den Rechner gesichert. Setlists im Pedal, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d parches suben en %s: %s%s\n\nCada plaza movida se guarda antes en el ordenador. Las setlists del pedal que apuntan a números ya no coincidirán. ¿Continuar?',
        'Rücke auf':
         'Subiendo',
        'Fertig: %d Plätze aufgerückt, %d vorher gesichert':
         'Listo: %d plazas subidas, %d guardadas antes',
        'Aufgerückt: %d Plätze':
         'Subidas: %d plazas',
        'Kopie auf dem Rechner — Änderungen hier wirken nicht auf das Pedal.':
         'Copia en el ordenador: los cambios aquí no afectan al pedal.',
        'Patch%d wird aus der Kopie auf dem Rechner entfernt. Das Pedal bleibt unverändert. Fortfahren?':
         'Patch%d se eliminará de la copia en el ordenador. El pedal no cambia. ¿Continuar?',
        'Patch%d wird von der Karte entfernt. Vorher wird er auf dem Rechner gesichert. Fortfahren?':
         'Patch%d se eliminará de la tarjeta. Antes se guarda en el ordenador. ¿Continuar?',
        'Ja':
         'Sí',
        'Nein':
         'No',
        'Dem Python fehlen die Zertifikate. Auf macOS hilft „Install Certificates.command“ im Python-Ordner, oder: pip install certifi':
         'A Python le faltan los certificados. En macOS ejecuta «Install Certificates.command» en la carpeta de Python, o: pip install certifi',
        'GitHub ist nicht erreichbar: %s':
         'No se puede acceder a GitHub: %s',
        'unbekannter Grund':
         'motivo desconocido',
        'Gelöscht: %s':
         'Borrado: %s',
        'Fehler:':
         'Error:',
        'Platz löschen':
         'Borrar plaza',
        'Welcher Platz soll gelöscht werden? (z. B. 30 oder Patch30)':
         '¿Qué plaza borrar? (p. ej. 30 o Patch30)',
        'Platz löschen …':
         'Borrar plaza …',
        '%s ist nicht belegt — nichts zu löschen.':
         '%s está vacío: nada que borrar.',
        '%s wird aus %s gelöscht. Das läßt sich nicht rückgängig machen. Fortfahren?':
         '%s se borrará de %s. No se puede deshacer. ¿Continuar?',
        'Lösche':
         'Borrando',
        'Gelöscht:':
         'Borrado:',
        'Fehler beim Löschen:':
         'Error al borrar:',
        'Die Datei ist %s lang — das Pedal nimmt höchstens 10 Minuten je Loop an. Bitte im Hauptfenster kürzen oder teilen.':
         'El archivo dura %s: el pedal admite como máximo 10 minutos por bucle. Acórtalo o divídelo en la ventana principal.',
        'Dieser Ordner ist nicht lesbar: %s':
         'No se puede leer esta carpeta: %s',
        'Auswahl:':
         'Selección:',
        'Pegelprotokoll wählen':
         'Elegir el registro de nivel',
        'Audiodatei zum Teilen auswählen':
         'Elegir el archivo de audio a dividir',
        'Dateiname:':
         'Nombre del archivo:',
        'Speichern':
         'Guardar',
        'Bitte einen Dateinamen angeben.':
         'Introduce un nombre de archivo.',
        'Überschreiben':
         'Sobrescribir',
        '%s gibt es schon. Ersetzen?':
         '%s ya existe. ¿Reemplazar?',
        'Speicherkarte des JamMan wählen':
         'Elegir la tarjeta del JamMan',
        'Zielordner für die Sicherung':
         'Carpeta de destino de la copia',
        'Dateien':
         'Archivos',
        'Nach oben':
         'Subir',
        'Öffnen':
         'Abrir',
        'Bitte eine Datei auswählen.':
         'Elige un archivo.',
        'Alle Dateien':
         'Todos los archivos',
        'Die Datei konnte nicht geschrieben werden: %s':
         'No se pudo escribir el archivo: %s',
        'ohne':
         'ninguno',
        'ende':
         'final',
        'klick':
         'clic',
        '%d Patches ohne Ton — nur die Beschreibung ist geholt.':
         '%d parches sin audio: solo se ha obtenido la descripción.',
        'Ganzen Ordner aufs Pedal …':
         'Toda la carpeta al pedal …',
        'Ordner mit Patch-Ordnern wählen':
         'Elegir una carpeta con carpetas de patch',
        'Ganzen Ordner aufs Pedal':
         'Toda la carpeta al pedal',
        '%d Patch-Ordner werden in %s geschrieben. Belegte Plätze werden vorher gesichert, unveränderte übersprungen. Fortfahren?':
         'Se escribirán %d carpetas de patch en %s. Las plazas ocupadas se guardan antes, las sin cambios se omiten. ¿Continuar?',
        'internen Speicher':
         'la memoria interna',
        'der Karte':
         'la tarjeta',
        'Im Ordner liegen keine Patch-Ordner (erwartet: Patch01 … Patch200).':
         'La carpeta no contiene carpetas de patch (se espera: Patch01 … Patch200).',
        'Fertig: %d Patches geschrieben, %d übersprungen, %d vorher gesichert':
         'Listo: %d parches escritos, %d omitidos, %d guardados antes',
        'Fehler beim Schreiben:':
         'Error al escribir:',
        'Übertragen: %d Dateien · unverändert übersprungen: %d':
         'Transferidos: %d archivos · omitidos sin cambios: %d',
        'Zielplatz auf dem Pedal (z. B. 17 oder Patch17):':
         'Plaza de destino en el pedal (p. ej. 17 o Patch17):',
        'Unerwarteter Fehler':
         'Error inesperado',
        'Das Werkzeug wurde unerwartet beendet.':
         'La herramienta se detuvo de forma inesperada.',
        'Protokoll:':
         'Registro:',
        'Auswahl kopieren':
         'Copiar la selección',
        'Nichts markiert — bitte zuerst Text markieren.':
         'Nada seleccionado: selecciona primero el texto.',
        'Anleitung speichern':
         'Guardar las instrucciones',
        'Gespeichert.':
         'Guardado.',
        'Alles kopieren':
         'Copiar todo',
        'Als Datei speichern':
         'Guardar como archivo',
        'Anleitung':
         'Instrucciones',
        'Zum Umwandeln fehlt ffmpeg.':
         'Falta ffmpeg para la conversión.',
        'Die Programmdateien lassen sich hier nicht ersetzen: %s':
         'Los archivos del programa no se pueden sustituir aquí: %s',
        'Unerwarteter Fehler: %s':
         'Error inesperado: %s',
        'Aktualisieren':
         'Actualizar',
        'Die neueste Fassung wird von GitHub geladen und ersetzt die Programmdateien. Ihre Einstellungen und Ihre Karte bleiben unberührt. Fortfahren?':
         'La última versión se descarga de GitHub y sustituye los archivos del programa. Tus ajustes y tu tarjeta no se tocan. ¿Continuar?',
        'Jetzt aktualisieren?':
         '¿Actualizar ahora?',
        'Aktualisiert':
         'Actualizado',
        'Nicht aktualisiert':
         'No actualizado',
        'Fenster jetzt schließen?':
         '¿Cerrar la ventana ahora?',
        'GitHub ist nicht erreichbar.':
         'No se puede acceder a GitHub.',
        'Sie benutzen bereits die neueste Fassung (%s).':
         'Ya está usando la última versión (%s).',
        'Zur Fassung %s gibt es keine Datei zum Herunterladen.':
         'La versión %s no tiene archivo para descargar.',
        'Lade Fassung %s …':
         'Descargando la versión %s …',
        'Herunterladen fehlgeschlagen: %s':
         'Descarga fallida: %s',
        'Die heruntergeladene Datei ist kein gültiges ZIP.':
         'El archivo descargado no es un ZIP válido.',
        'Die Datei ist beschädigt (%s).':
         'El archivo está dañado (%s).',
        'In der Datei fehlt jamman.py.':
         'Falta jamman.py en el archivo.',
        'Die Datei gehört nicht zur Fassung %s.':
         'El archivo no corresponde a la versión %s.',
        'Fassung %s ist installiert (%d Dateien). Bitte das Fenster schließen und neu starten.':
         'La versión %s está instalada (%d archivos). Cierre la ventana y vuelva a iniciar.',
        'Die Karte hängt nicht im Rechner — nichts auszuwerfen. Steckt sie im Pedal, führt das Pedal sie nicht als Laufwerk.':
         'La tarjeta no está en el ordenador: no hay nada que expulsar. Si está en el pedal, este no la ofrece como unidad.',
        'Auswerfen ging nicht. Bitte im Dateimanager auf „Auswerfen" klicken.':
         'No se pudo expulsar. Usa «Expulsar» en el gestor de archivos.',
        'Keine Karte im Rechner gefunden.':
         'No se ha encontrado ninguna tarjeta en el ordenador.',
        'Karte im Rechner:  „Karte wählen“ drücken.':
         'Tarjeta en el ordenador: pulsa «Elegir tarjeta».',
        'Karte im Pedal:    oben rechts „Pedal über USB“ — dort sind Karte und interner Speicher erreichbar.':
         'Tarjeta en el pedal: arriba a la derecha «Pedal por USB» — allí se accede a la tarjeta y a la memoria interna.',
        'Speicherkarte': 'Tarjeta de memoria',
        'Kopie vom Pedal': 'copia del pedal',
        'Speicherkarte des JamMan Stereo': 'Tarjeta de memoria del JamMan Stereo',
        'Im Pedal steckt keine Karte.': 'No hay tarjeta en el pedal.',
        'Karte einlegen und dann „Karte wählen“ oder „Neu laden“ drücken.':
         'Inserta la tarjeta y pulsa «Elegir tarjeta» o «Recargar».',
        'keine Karte':
         'sin tarjeta',
        'Dies ist eine Kopie vom Pedal, nicht die Karte selbst. Über „Karte wählen“ die Karte öffnen.':
         'Esta es una copia tomada del pedal, no la tarjeta. Usa «Elegir tarjeta».',
        'JamMan Card Tool': 'JamMan Card Tool',
        'Karte und interner Speicher': 'tarjeta y memoria interna',
        '(für Rückgängig nötig)':
         '(necesaria para Deshacer)',
        'Darunter ist die Sicherung für Rückgängig. Danach läßt sich der letzte Schreibvorgang nicht mehr zurücknehmen. Trotzdem löschen?':
         'Incluye la copia para Deshacer. Después ya no se podrá deshacer la última escritura. ¿Borrar igualmente?',
        'Sicherungen aufräumen …':
         'Limpiar copias …',
        'Sicherungen aufräumen':
         'Limpiar copias',
        'Sicherungen im Zielordner':
         'Copias en la carpeta de destino',
        '%d Sicherungen · %.1f MB':
         '%d copias · %.1f MB',
        'Neueste behalten:':
         'Conservar las más recientes:',
        'Markierte löschen':
         'Borrar seleccionadas',
        'Erst Sicherungen in der Liste markieren.':
         'Primero marca copias en la lista.',
        '%d Sicherungen mit %.1f MB werden endgültig gelöscht. Fortsetzen?':
         'Se borrarán definitivamente %d copias (%.1f MB). ¿Continuar?',
        '%d Sicherungen konnten nicht gelöscht werden.':
         'No se pudieron borrar %d copias.',
        'Der Zielordner ist noch nicht angelegt.':
         'La carpeta de destino aún no existe.',
        'Im Zielordner liegen keine Sicherungen.':
         'No hay copias en la carpeta de destino.',
        'Alle auswählen':
         'Seleccionar todo',
        'Nichts markiert. Alle %d Patches in die Setlist?':
         'Nada seleccionado. ¿Poner los %d patches en la setlist?',
        'Warte auf das Ende des laufenden Vorgangs …':
         'Esperando a que termine la operación en curso …',
        'Der Vorgang reagiert nicht — Fenster schließen und neu öffnen.':
         'La operación no responde: cierra la ventana y ábrela de nuevo.',
        'Hole Beschreibungen': 'Leyendo descripciones',
        'Es wurde kein Zielplatz eingegeben.':
         'No se ha introducido ninguna ranura de destino.',
        'Der Zielplatz muß wie Patch01 … Patch200 heißen, nicht „%s“.':
         'La ranura debe llamarse como Patch01 … Patch200, no «%s».',
        'OK': 'OK',
        'Ein Vorgang läuft noch — bitte abwarten.':
         'Hay una operación en curso: espera por favor.',
        'Verbindung neu aufbauen':
         'Reconectar',
        'Verbindung wird neu aufgebaut …':
         'Reconectando …',
        'Lesen und Schreiben über USB. Vor jedem Überschreiben wird der alte Platz auf den Rechner gesichert.':
         'Lectura y escritura por USB. Antes de sobrescribir, la ranura anterior se guarda en el ordenador.',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller. Schreiben verändert die Karte — vorher wird gesichert.':
         'Por USB circulan unos 1,3 MB/s. Para una tarjeta completa, un lector es mucho más rápido. Escribir modifica la tarjeta: se guarda antes.',
        'Audio in den Speicher schreiben …': 'Escribir audio en la memoria …',
        'Beim JamMan Solo XT behält der FS3X seine Grundfunktionen. Eine eigene Belegung speichert das Pedal zwar, wertet sie aber nicht aus — deshalb wird sie hier nicht angeboten.':
         'En el JamMan Solo XT, el FS3X conserva sus funciones básicas. El pedal guarda una asignación propia pero no la aplica, por eso no se ofrece aquí.',
        'Änderungen wirken nach einem Neustart des Pedals.': 'Los cambios surten efecto tras reiniciar el pedal.',
        'gewünscht': 'pedido',
        'Pedal meldet': 'el pedal informa',
        '(nicht belegt)':
         '(sin asignar)',
        'UndoRedo = Rückgängig/Wiederholen · Reverse = Rückwärts · Tempo = Tempo antippen':
         'UndoRedo = deshacer/rehacer · Reverse = hacia atrás · Tempo = marcar tempo',
        'Sichern': 'Guardar',
        'Schreiben': 'Escribir',
        'Setlists': 'Setlists',
        'Pedal': 'Pedal',
        'Ins Hauptfenster holen …': 'Traer a la ventana principal …',
        'Einzelnen Patch sichern …': 'Guardar un solo patch …',
        'Patch-Ordner aufs Pedal …': 'Carpeta de patch al pedal …',
        'Übernommen.': 'Aplicado.',
        'Pedal spielt jetzt:': 'El pedal reproduce ahora:',
        'Audio aufs Pedal …':
         'Audio al pedal …',
        'Audio aufs Pedal':
         'Audio al pedal',
        'Audiodatei wählen':
         'Elegir un archivo de audio',
        'Audio':
         'Audio',
        'Sichere den alten Platz …':
         'Guardando la ranura anterior …',
        'Wandle die Datei …':
         'Convirtiendo el archivo …',
        'Umwandeln fehlgeschlagen — die Datei muß 44,1 kHz, 16 Bit, Stereo werden.':
         'Conversión fallida: el archivo debe quedar en 44,1 kHz, 16 bits, estéreo.',
        'Leere den Platz …': 'Vaciando la ranura …',
        'Pedal spielt jetzt Bank/Patch:': 'El pedal reproduce ahora banco/patch:',
        'Karte erkannt:': 'Tarjeta detectada:',
        'Als ZIP sichern …':
         'Guardar como ZIP …',
        'Als ZIP sichern':
         'Guardar como ZIP',
        'Sichere als ZIP …':
         'Guardando como ZIP …',
        'Sichere:':
         'Guardando:',
        'Fehler beim Sichern:':
         'Error al guardar:',
        'Die Setlist ist leer.':
         'La setlist está vacía.',
        'Erst Patches in der Liste markieren (⌘-Klick).':
         'Primero marca patches en la lista (⌘-clic).',
        'Es gibt nichts rückgängig zu machen.':
         'No hay nada que deshacer.',
        'Fußschalter …':
         'Pulsador …',
        'Fußschalter':
         'Pulsador',
        'Tastenbelegung des Pedals':
         'Asignación del pulsador del pedal',
        'Werte sind Geräteeinstellungen — nur ändern, wenn du weißt, was sie bedeuten.':
         'Son ajustes del dispositivo: cámbialos solo si sabes qué significan.',
        'JamMan':
         'JamMan',
        'Karte':
         'tarjeta',
        'intern':
         'interno',
        'Liste als Textdatei sichern':
         'Guardar la lista como archivo de texto',
        'Liste als Textdatei …':
         'Lista como archivo de texto …',
        'Mitlaufen':
         'Seguir',
        'Mitlaufen an — am Pedal einen Patch wählen.':
         'Seguimiento activado: elige un patch en el pedal.',
        'Mitlaufen aus.':
         'Seguimiento desactivado.',
        'Rückgängig':
         'Deshacer',
        'Setlist holen …':
         'Traer setlist …',
        'Setlist laden':
         'Cargar setlist',
        'Setlist sichern …':
         'Guardar setlist …',
        'Setlist sichern':
         'Guardar setlist',
        'Setlist.txt':
         'Setlist.txt',
        'Stelle wieder her:':
         'Restaurando:',
        '%s wird durch die Sicherung ersetzt. Fortsetzen?':
         '%s se sustituirá por la copia de seguridad. ¿Continuar?',
        'Ordner des Patches wählen':
         'Elegir la carpeta del patch',
        'Aufs Pedal schreiben':
         'Escribir en el pedal',
        '%s ist schon belegt. Der Patch wird vorher auf den Rechner gesichert und dann überschrieben. Fortsetzen?':
         '%s ya está ocupado. Ese patch se guardará primero en el ordenador y luego se sobrescribirá. ¿Continuar?',
        'Schreibe aufs Pedal …':
         'Escribiendo en el pedal …',
        'Schreibe:':
         'Escribiendo:',
        'Patch aufs Pedal schreiben …':
         'Escribir patch en el pedal …',
        '(Vorschlag)': '(propuesta)',
        'Ziel:': 'Destino:',
        'noch nicht gewählt': 'aún no elegido',
        'Ziel ändern …': 'Cambiar destino …',
        'Aufräumen':
         'Limpiar',
        'Die angefangene Kopie entfernen? Nur die gerade geholten Dateien werden gelöscht, der Ordner bleibt.':
         '¿Eliminar la copia inacabada? Solo se borran los archivos recién traídos; la carpeta permanece.',
        'Aufgeräumt:':
         'Limpiado:',
        'Hole Beschreibungen …': 'Leyendo descripciones …',
        'Wird abgebrochen …': 'Cancelando …',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller — steckt die Karte im Pedal, bleibt nur dieser Weg.':
         'Por USB circulan unos 1,3 MB/s. Para una tarjeta completa, un lector es mucho más rápido — si la tarjeta está en el pedal, este es el único camino.',
        'Abbrechen': 'Cancelar',
        'Abgebrochen.': 'Cancelado.',
        'Fehler beim Öffnen:': 'Error al abrir:',
        'Alle ins Hauptfenster holen …': 'Traer todo a la ventana principal …',
        'Zielordner wählen': 'Elegir la carpeta de destino',
        'Hole Patch': 'Leyendo patch',
        'Fertig:': 'Listo:',
        'Im Hauptfenster öffnen?': '¿Abrir en la ventana principal?',
        'Gesichert': 'Guardado',
        'Pedal über USB': 'Pedal por USB',
        'Das USB-Modul fehlt: greenglass.py muß neben jamman.py liegen.': 'Falta el módulo USB: greenglass.py debe estar junto a jamman.py.',
        'Gesichert nach:': 'Guardado en:',
        'Interner Speicher': 'Memoria interna',
        'SD-Karte': 'Tarjeta SD',
        'Kein JamMan gefunden — Pedal einschalten und Kabel prüfen.': 'No se ha encontrado ningún JamMan — encienda el pedal y compruebe el cable.',
        '(ohne Namen)': '(sin nombre)',
        'Fehler beim Lesen:': 'Error al leer:',
        'Erst „Neu laden“ drücken.': 'Pulse primero «Recargar».',
        'Lädt …': 'Cargando …',
        'Nur lesen: das Pedal wird nicht verändert.': 'Solo lectura: el pedal no se modifica.',
        'Ordner zum Sichern wählen': 'Elegir una carpeta para guardar',
        'Patch sichern …': 'Guardar patch …',
        'Verbinde mit dem Pedal …': 'Conectando con el pedal …',
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
        "BEFEHL": "ORDEN",
        "  Auswahl: ": "  Opción: ",
        "  Anderer Pfad (leer = beenden): ": "  Otra ruta (vacío = salir): ",
        "  Patch-Nummer: ": "  Número del patch: ",
        "  Name (leer = Dateiname): ": "  Nombre (vacío = nombre del archivo): ",
        "  Stopp (fade/instant, leer = wie Vorlage): ": "  Parada (fade/instant, vacío = como plantilla): ",
        "  Wiedergabe (loop/single, leer = wie Vorlage): ": "  Reproducción (loop/single, vacío = como plantilla): ",
        "  Pfad zur Audiodatei: ": "  Ruta del archivo de audio: ",
        "  Stopp (fade/instant, leer = lassen): ": "  Parada (fade/instant, vacío = dejar): ",
        "  Wiedergabe (loop/single, leer = lassen): ": "  Reproducción (loop/single, vacío = dejar): ",
        "  Neuer Name (leer = lassen): ": "  Nombre nuevo (vacío = dejar): ",
        "  Wirklich schreiben? (j/n): ": "  ¿Escribir de verdad? (s/n): ",
        "  Welcher Patch: ": "  ¿Qué patch?: ",
        "  An welche Stelle: ": "  ¿A qué plaza?: ",
        "  Wirklich verschieben? (j/n): ": "  ¿Mover de verdad? (s/n): ",
        "  Zielordner (leer = ~/JamMan-Sicherung): ": "  Carpeta de destino (vacío = ~/JamMan-Sicherung): ",
        "  Wirklich löschen? (j/n): ": "  ¿Eliminar de verdad? (s/n): ",
        "Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay).": "No se encontró ningún reproductor (aplay, paplay, mpv o ffplay).",
        "Fehler: im Protokoll stehen keine Einträge.": "Error: el protocolo no tiene entradas.",
        "Fehler: Datei ließ sich nicht umwandeln.": "Error: el archivo no se pudo convertir.",
        "Fehler: kein vollständiger Patch als Vorlage gefunden.": "Error: no se encontró ningún patch completo como plantilla.",
        "Fehler: nichts angegeben (--stopp, --rhythmus, --wiedergabe, --name oder --fade).": "Error: no se indicó nada (--stopp, --rhythmus, --wiedergabe, --name o --fade).",
        "Fehler: '%s' ist keine lesbare Audiodatei.": "Error: '%s' no es un archivo de audio legible.",
        "Fehler: --teile N oder --laenge SEKUNDEN angeben.": "Error: indique --teile N o --laenge SEGUNDOS.",
        "Fehler: %.1f Minuten pro Teil sind zu lang (Grenze %d Minuten).": "Error: %.1f minutos por parte es demasiado (límite %d minutos).",
        "Fehler: '%s' gibt es nicht.": "Error: '%s' no existe.",
        "Fehler: '%s' ist keine lesbare Audiodatei.%s": "Error: '%s' no es un archivo de audio legible.%s",
        "Fehler: '%s' liegt schon vor.": "Error: '%s' ya existe.",
        "Fehler: Die Datei ließ sich nicht kürzen.": "Error: el archivo no se pudo acortar.",
        "Fehler: Die Umwandlung hat keine hörbare Aufnahme\n  ergeben (nur Stille). Datei prüfen: %s": "Error: la conversión no produjo ninguna grabación audible\n  (solo silencio). Revisar el archivo: %s",
        "Fehler: Patch%d gibt es nicht.": "Error: el patch %d no existe.",
        "Fehler: Die Aufnahme ließ sich nicht ausblenden (kein 16-Bit-WAV?).": "Error: la grabación no se pudo atenuar (¿no es un WAV de 16 bits?).",
        "Fehler: '%s' ist nicht eingelegt.": "Error: '%s' no está insertada.",
        "Fehler beim Auswerfen: %s": "Error al expulsar: %s",
        "Fehler: Patch%d hat keine WAV.": "Error: el patch %d no tiene WAV.",
        "Fehler: Sicherungsordner '%s' gibt es nicht.": "Error: la carpeta de copia '%s' no existe.",
        "Fehler: in '%s' liegen keine Patch-Ordner.": "Error: '%s' no contiene carpetas de patch.",
        "Fehler: keine Sicherung mit einer Aufnahme für Patch%d in '%s'.\n  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade.": "Error: no hay copia con una grabación para el patch %d en '%s'.\n  Las copias de fundido se llaman Sicherung_<fecha>_vor_fade.",
        "Fehler: Platz %d ist belegt (%s).\n  Zum Tauschen:  tauschen --patch %d --mit %d": "Error: la plaza %d está ocupada (%s).\n  Para intercambiar:  tauschen --patch %d --mit %d",
        "Fehler: Nummern müssen zwischen 1 und %d liegen.": "Error: los números deben estar entre 1 y %d.",
        "Fehler: '%s' ist ein Ordner, keine Datei.": "Error: '%s' es una carpeta, no un archivo.",
        "Fehler: '%s' ist kein Pegelprotokoll (keine lesbare Textdatei).": "Error: '%s' no es un protocolo de nivel (no es un archivo de texto legible).",
        "Fehler: '%s' darf nicht gelesen werden.": "Error: '%s' no se puede leer.",
        "Fehler: '%s' lässt sich nicht lesen: %s": "Error: '%s' no se puede leer: %s",
        "Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n  ffmpeg installieren.": "Error al convertir. El archivo tiene %d Hz / %d canal(es);\n  se necesitan 44100 Hz y 2 canales. Convertirlo antes o\n  instalar ffmpeg.",
        "Fehler: '%s' lässt sich nicht schreiben.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Error: '%s' no se puede escribir.\n  Elija otra carpeta:  --sicherung <ruta>",
        "Fehler: Patch%d hat keine phrase.wav.": "Error: el patch %d no tiene phrase.wav.",
        "Fehler: Der Pegel ließ sich nicht ändern.": "Error: el nivel no se pudo cambiar.",
        "Fehler: in '%s' darf nicht geschrieben werden.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "Error: en '%s' no se puede escribir.\n  Elija otra carpeta:  --sicherung <ruta>",
        "Fehler: Teil %d ließ sich nicht schneiden.": "Error: la parte %d no se pudo cortar.",
        "Fehler: Die Datei ist %.1f Minuten lang.\n  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n  nicht erkannt - und können dabei verschwinden.\n\n  So geht es weiter:\n    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n                         12 ms ausgeblendet, damit es nicht knackt)\n    --laenge 480         auf 8:00 kürzen\n    --trotzdem           unverändert schreiben (zum Ausprobieren)": "Error: el archivo dura %.1f minutos.\n  El JamMan Solo XT acepta como máximo %d minutos por loop\n  (manual, página 60: \"Max. Individual Loop Time: 10 minutes\").\n  Los loops más largos aparecen en la tarjeta, pero el pedal no\n  los reconoce - y pueden desaparecer.\n\n  Qué se puede hacer:\n    --kuerzen            acortar a 9:59 (corte duro al final,\n                         12 ms atenuados para que no chasquee)\n    --laenge 480         acortar a 8:00\n    --trotzdem           escribir sin cambios (para probar)",
        "Fehler: --wav fehlt.": "Error: falta --wav.",
        "Fehler: --patch fehlt.": "Error: falta --patch.",
        "Fehler: --protokoll fehlt.": "Error: falta --protokoll.",
        "Fehler: --patch fehlt (oder --alle angeben).": "Error: falta --patch (o indicar --alle).",
        "Fehler: --patch und --auf fehlen.": "Error: faltan --patch y --auf.",
        "Fehler: --patch und --mit fehlen.": "Error: faltan --patch y --mit.",
        "Fehler: --patch und --nach fehlen.": "Error: faltan --patch y --nach.",
        "  Hinweis: Pegelprotokoll konnte nicht geschrieben werden.": "    Nota: no se pudo escribir el protocolo de nivel.",
        "  Protokoll: %s": "    Protocolo: %s",
        "  %d Einträge": "    %d entradas",
        "    %-10s %-24s Faktor %.4f → 1/%.4f": "      %-10s %-24s factor %.4f → 1/%.4f",
        "  Trockenlauf — nichts geändert. Mit --schreiben ausführen.": "    Simulación — nada cambiado. Ejecutar con --schreiben para escribir.",
        "    %-10s %+6.1f → %+6.1f dB": "      %-10s %+6.1f → %+6.1f dB",
        "  Zurückgerechnet: %d Patches. Sicherung davor: %s": "    Recalculado: %d patches. Copia previa: %s",
        "  Pegel:        %.1f dB → %.1f dB Spitze": "    Nivel:        %.1f dB → %.1f dB de pico",
        "                Die Anhebung wurde begrenzt, damit nichts": "                La subida se limitó para que nada",
        "                über 0 dBFS geht (sonst würde übersteuert).": "                supere 0 dBFS (si no, saturaría).",
        "                Achtung: %d Werte liegen an der Grenze.": "                Atención: %d valores están en el límite.",
        "  Hinweis: unbekanntes WAV-Format 0x%04X - nicht angefasst.": "    Nota: formato WAV desconocido 0x%04X — sin tocar.",
        "Fehler: '%s' ist kein Ordner. Ist die Karte eingelegt?": "  Error: '%s' no es una carpeta. ¿Está insertada la tarjeta?",
        "Fehler: auf '%s' liegt kein Ordner '%s' oder '%s'. Das ist keine JamMan-Karte.": "  Error: '%s' no contiene la carpeta '%s' ni '%s'. No es una tarjeta JamMan.",
        "  %d Apple-Dateien entfernt (._* und .DS_Store).": "    %d archivos Apple eliminados (._* y .DS_Store).",
        "  Keine Patches auf der Karte.": "    No hay patches en la tarjeta.",
        "  %-10s %-34s %8s  %s": "    %-10s %-34s %8s  %s",
        "  %-10s %-34s %8s  %s%s%s": "    %-10s %-34s %8s  %s%s%s",
        "\n  %d Patches, %s belegt.": "\n  %d patches, %s ocupados.",
        "  Sicherung beim Start: Karte unverändert, nichts zu tun.": "    Copia al inicio: tarjeta sin cambios, nada que hacer.",
        "  Sicherung beim Start: %s": "    Copia al inicio: %s",
        "    gesichert: %d Patches, %.0f MB": "      guardados: %d patches, %.0f MB",
        "    alte Sicherung entfernt: %s": "      copia antigua eliminada: %s",
        "  Quelle: %s": "    Origen: %s",
        "  Ziel:   %s": "    Destino: %s",
        "  Trockenlauf — nichts kopiert. Mit --schreiben ausführen.": "    Simulación — nada copiado. Ejecutar con --schreiben para escribir.",
        "  Gesichert: %d Patches, %.0f MB.": "    Guardado: %d patches, %.0f MB.",
        "  Datei:  %s": "    Archivo: %s",
        "  Dauer:  %s": "    Duración: %s",
        "  Teile:  %d × etwa %s": "    Partes: %d × unos %s",
        "  Suche musikalisch brauchbare Schnittstellen …": "    Buscando puntos de corte utilizables …",
        "  Hinweis: keine brauchbaren Pausen gefunden (%s) -": "    Nota: no se encontraron pausas utilizables (%s) —",
        "           es wird gleichmäßig geschnitten.": "             se corta por igual.",
        "  Pause an der Naht wird herausgenommen": "    Se quita la pausa en la unión",
        "    Teil %d: %7.1f – %7.1f s  (%s)": "      Parte %d: %7.1f – %7.1f s  (%s)",
        "  Hinweis: Teil %d wäre %.1f min - er wird auf 9:59 gekürzt.": "    Nota: la parte %d sería %.1f min — se acorta a 9:59.",
        "    Teil %d: %s – %s": "      Parte %d: %s – %s",
        "  Trockenlauf — nichts angelegt. Mit --schreiben ausführen.": "    Simulación — nada creado. Ejecutar con --schreiben para escribir.",
        "  Quelle:       %s": "    Origen:       %s",
        "  Dauer:        %s": "    Duración:     %s",
        "  Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s": "    Formato:      %d Hz / %d bit / %d canal(es)%s",
        "  Stopp:        %s": "    Parada:       %s",
        "  Rhythmus:     %s": "    Ritmo:        %s",
        "  Wiedergabe:   %s": "    Reproducción: %s",
        "  Fade:         letzte %.1f s werden ausgeblendet": "    Fundido:      los últimos %.1f s se atenúan",
        "  Pegel:        auf -1 dB Spitze normieren": "    Nivel:        normalizar a -1 dB de pico",
        "  Pegel:        %+.1f dB": "    Nivel:        %+.1f dB",
        "  Neuer Patch:  Patch%s  (Name: %s)": "    Nuevo patch:  Patch%s  (nombre: %s)",
        "  Vorlage:      %s": "    Plantilla:    %s",
        "  Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.": "    Simulación — nada escrito. Ejecutar con --schreiben para escribir.",
        "  Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)": "    Acortar:      cortado a %s (corte duro, 12 ms atenuados)",
        "  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben": "    Cabecera:     cabecera WAV reescrita a PCM simple",
        "  Fertig: %s angelegt.": "    Hecho: %s creado.",
        "  Karte auswerfen, in den JamMan stecken — der Patch erscheint als Patch%s.": "    Expulsa la tarjeta, insértela en el JamMan — el patch aparece como Patch%s.",
        "  Patch:        %s  (%s)": "    Patch:        %s  (%s)",
        "  Stopp:        %s (%s)": "    Parada:       %s (%s)",
        "  Rhythmus:     %s (%s)": "    Ritmo:        %s (%s)",
        "  Wiedergabe:   %s (IsLoop %s)": "    Reproducción: %s (IsLoop %s)",
        "  Name:         %s": "    Nombre:       %s",
        "  Fade:         letzte %.1f s der Aufnahme ausblenden": "    Fundido:      se atenúan los últimos %.1f s de la grabación",
        "                (verändert die Audiodatei; vorher wird gesichert)": "                (modifica el archivo de audio; antes se hace una copia)",
        "  Geändert. Sicherung: %s": "    Cambiado. Copia: %s",
        "  %s Sie kann jetzt abgezogen werden.": "    %s Ya se puede retirar.",
        "  Schreibweise des Geräts: Patch%0*d": "    Escritura del aparato: Patch%0*d",
        "  Alle Ordner heißen schon so. Nichts zu tun.": "    Todas las carpetas ya están así. Nada que hacer.",
        "  Zu ändern: %d": "    A cambiar: %d",
        "    %-12s ->  %s": "      %-12s ->  %s",
        "    %-12s FEHLER: %s": "      %-12s ERROR: %s",
        "  Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.": "    Hecho: %d carpetas renombradas, %d errores, %d patches legibles.",
        "  Zuordnung liegt in: %s": "    La asignación está en: %s",
        "  %d Apple-Dateien auf der Karte. Mit --schreiben entfernen.": "    %d archivos Apple en la tarjeta. Quitarlos con --schreiben.",
        "  Fertig.": "    Hecho.",
        "  Spielt \"%s\" (%s) … (Strg+C hält an)": "    Reproduciendo \"%s\" (%s) … (Ctrl+C detiene)",
        "  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB": "    Objetivo: %.1f dB de sonoridad (RMS), pico como máximo %.1f dB",
        "  Patches: %d": "    Patches: %d",
        "  %-10s %-24s %8s": "    %-10s %-24s %8s",
        "  %-10s %-24s %+7.1f dB": "    %-10s %-24s %+7.1f dB",
        "  Sicherung: %s": "    Copia: %s",
        "  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s": "    %-10s %-24s %+6.1f → %+6.1f dB  (pico %+.1f dB)%s",
        "  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.": "    Hecho: %d patches normalizados, %d de ellos limitados en el pico.",
        "  Patches in der Sicherung: %d": "    Patches en la copia: %d",
        "  Patches auf der Karte:    %d": "    Patches en la tarjeta:    %d",
        "  Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:": "    Nota: estos no existen en la tarjeta y se crearán:",
        "           %s": "             %s",
        "  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.": "    Simulación — nada restaurado. Ejecutar con --schreiben para escribir.",
        "  Zurückgeholt. Der Zustand davor liegt in: %s": "    Restaurado. El estado anterior está en: %s",
        "  Patch:      %s  (%s)": "    Patch:      %s  (%s)",
        "  Sicherung:  %s": "    Copia:      %s",
        "    %-22s %8.2f MB": "      %-22s %8.2f MB",
        "  %s  (%s)": "    %s  (%s)",
        "  →  %s": "    →  %s",
        "  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.": "    Simulación — nada renombrado. Ejecutar con --schreiben para escribir.",
        "  Fertig. Sicherung: %s": "    Hecho. Copia: %s",
        "  Nichts zu tun.": "    Nada que hacer.",
        "  Achtung: %s und %s haben unterschiedliche Stellenzahl.": "    Atención: %s y %s tienen distinto número de dígitos.",
        "           Der JamMan sortiert nach Namen - der Patch landet dann": "             El JamMan ordena por nombre — el patch irá",
        "           auf dem Platz, den sein neuer Name angibt.": "             a la plaza que indique su nuevo nombre.",
        "  Tauschen:": "    Intercambio:",
        "    Platz %-4s %-24s  →  Platz %s": "      plaza %-4s %-24s  →  plaza %s",
        "  Trockenlauf — nichts getauscht. Mit --schreiben ausführen.": "    Simulación — nada intercambiado. Ejecutar con --schreiben para escribir.",
        "  Getauscht. Sicherung: %s": "    Intercambiado. Copia: %s",
        "  Wird gelöscht: %s  (%s)": "    Se eliminará: %s  (%s)",
        "  Vorher wird er gesichert nach: %s": "    Antes se guarda en: %s",
        "  Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.": "    Simulación — nada eliminado. Ejecutar con --schreiben para escribir.",
        "  Gelöscht. Sicherung: %s": "    Eliminado. Copia: %s",
        "  Karte auswerfen, in den JamMan stecken — der Patch ist weg.": "    Expulsa la tarjeta, insértela en el JamMan — el patch ya no está.",
        "  Neue Reihenfolge:": "    Nuevo orden:",
        "    Platz %d:  %-10s  %s%s": "      plaza %d:  %-10s  %s%s",
        "  Verschoben. Sicherung: %s": "    Movido. Copia: %s",
        "  JamMan Solo XT — Speicherkarte verwalten": "    JamMan Solo XT — gestionar la tarjeta",
        "  Keine Karte gefunden unter %s.": "    No se encontró tarjeta en %s.",
        "  Karte: %s": "    Tarjeta: %s",
        "  1  Patches auflisten": "    1  Listar patches",
        "  2  Patch anhören": "    2  Escuchar un patch",
        "  3  Datei aufspielen": "    3  Cargar un archivo",
        "  4  Patch ändern (Stopp, Wiedergabe, Name)": "    4  Cambiar un patch (parada, reproducción, nombre)",
        "  5  Patch verschieben": "    5  Mover un patch",
        "  6  Patch löschen": "    6  Eliminar un patch",
        "  7  Alles sichern": "    7  Copiar todo",
        "  8  Karte auswerfen": "    8  Expulsar la tarjeta",
        "  0  Beenden": "    0  Salir",
        "  %s": "    %s",
        "  System:        %s %s": "    Sistema:       %s %s",
        "  Python:        %s": "    Python:        %s",
        "  Fassung:       %s": "    Versión:       %s",
        "  Tk:            %s": "    Tk:            %s",
        "  Tk-Modul:      %s": "    Módulo Tk:     %s",
        "  Tk:            NICHT verfügbar — %s": "    Tk:            NO disponible — %s",
        "                 (Fenster geht mit diesem Python nicht;": "                    (la ventana no funciona con este Python;",
        "                  das Textmenü geht trotzdem: jamman.py menu)": "                     el menú de texto sí: jamman.py menu)",
        "  Ton:           winsound (Windows-eigen)": "    Sonido:        winsound (integrado en Windows)",
        "  Ton:           afplay": "    Sonido:        afplay",
        "  Ton:           %s": "    Sonido:        %s",
        "  Kartenwurzeln: %s": "    Raíces de tarjetas: %s",
        "  Einstellungen: %s": "    Ajustes:       %s",
        "  ffmpeg:        %s": "    ffmpeg:        %s",
        "  Schrift:       %s": "    Fuente:        %s",
        "  Pythons auf diesem Rechner:": "    Pythons en esta máquina:",
        "    %-62s %s": "      %-62s %s",
        "  Geprüfte Orte:": "    Lugares comprobados:",
        "  Laufwerke mit JamManSoloXT:": "    Unidades con JamManSoloXT:",
        "    %-24s %3d Patches  %s": "      %-24s %3d patches  %s",
        "  gewählt:       %s": "    elegido:       %s",
        "  Patches:       %d": "    Patches:       %d",
        "  ACHTUNG: das Dateisystem liefert Lesefehler.": "    ATENCIÓN: el sistema de archivos informa errores de lectura.",
        "  Betroffene Einträge (erste drei): %s": "    Entradas afectadas (tres primeras): %s",
        "  Inhalt von %s:": "    Contenido de %s:",
        "    %-24r %s, %s": "      %-24r %s, %s",
        "    nicht lesbar: %s": "      ilegible: %s",
        "    %-10s %s": "      %-10s %s",
        "  erste Zeile im Fenster:": "    primera línea en la ventana:",
        "    %r": "      %r",
        "JamMan Solo XT": "  JamMan Solo XT",
        "WAV-Format": "Formato WAV",
        "%d Hz / %d Bit / %d K": "%d Hz / %d bit / %d can",
        "  %-28s  (gibt es nicht)": "  %-28s  (no existe)",
        "  %-28s  (nicht lesbar: %s)": "  %-28s  (ilegible: %s)",
        "  %-28s  (leer)": "  %-28s  (vacío)",
        "  %-28s  %s mit %s Patches": "  %-28s  %s con %s patches",
        "kein nutzbares Tk": "ningún Tk utilizable",
        "Sprache der Ausgabe (Vorgabe: aus dem System)": "Idioma de salida (por defecto: el del sistema)",
        "Laufwerk der Karte (Vorgabe: automatisch erkannt)": "Unidad de la tarjeta (por defecto: detectada automáticamente)",
        "Zielordner für 'sichern'": "Carpeta de destino para «copiar»",
        "Audiodatei für 'aufspielen'": "Archivo de audio para «cargar»",
        "Name des Patches (Vorgabe: Dateiname)": "Nombre del patch (por defecto: nombre del archivo)",
        "Patch-Nummer": "Número del patch",
        "Zielplatz für 'verschieben'": "Plaza de destino para «mover»",
        "Tauschpartner für 'tauschen'": "Compañero de intercambio para «intercambiar»",
        "Zielplatz für 'platzieren'": "Plaza de destino para «colocar»",
        "Sicherungsordner für 'zuruecknehmen'": "Carpeta de copia para «restaurar»",
        "Pegelprotokoll für 'pegel-umkehren'": "Protocolo de nivel para «deshacer el nivel»",
        "bei 'zuruecknehmen': alle Patches aus der Sicherung": "en «restaurar»: todos los patches de la copia",
        "Ziellautheit für 'alle-normieren' (Vorgabe -18 dB)": "Volumen objetivo para «normalizar todo» (por defecto -18 dB)",
        "Spitzengrenze für 'alle-normieren' (Vorgabe -1 dB)": "Límite de pico para «normalizar todo» (por defecto -1 dB)",
        "zu lange Datei auf 9:59 kürzen": "acortar un archivo demasiado largo a 9:59",
        "beim Teilen die Pause an der Naht stehen lassen": "al dividir, dejar la pausa en la unión",
        "beim Teilen gleichmäßig schneiden statt an Pausen": "al dividir, cortar por igual en vez de en las pausas",
        "Datei in N gleich lange Patches aufteilen": "dividir el archivo en N patches de igual duración",
        "zu lange Datei auf diese Länge kürzen": "acortar el archivo demasiado largo a esta duración",
        "auch Loops über 10 Minuten schreiben (Pedal nimmt sie nicht an)": "escribir también loops de más de 10 minutos (el pedal no los acepta)",
        "Fade am Ende der Datei einrechnen (z. B. 3)": "tener en cuenta el fundido al final del archivo (p. ej. 3)",
        "Lautstärke um DB ändern (z. B. -3 oder +2)": "cambiar el volumen en DB (p. ej. -3 o +2)",
        "Anhebung NICHT auf 0 dBFS begrenzen (kann übersteuern)": "NO limitar la subida a 0 dBFS (puede saturar)",
        "auf -1 dB Spitzenpegel normieren": "normalizar a -1 dB de pico",
        "Stopp-Verhalten des neuen Patches (ohne Angabe: wie Vorlage)": "comportamiento de parada del nuevo patch (sin indicación: como la plantilla)",
        "Loop oder Single (IsLoop in phrase.xml; ohne Angabe: wie Vorlage)": "loop o single (IsLoop en phrase.xml; sin indicación: como la plantilla)",
        "Rhythmus des neuen Patches (ohne Angabe: wie Vorlage)": "ritmo del nuevo patch (sin indicación: como la plantilla)",
        "beim Verschieben nur die Namen sichern (schnell, kein Kopieren)": "al mover, guardar solo los nombres (rápido, sin copiar)",
        "Ordner für die Sicherung vor Löschen/Verschieben": "carpeta para la copia antes de borrar/mover",
        "wirklich verändern (sonst Trockenlauf)": "cambiar de verdad (si no, simulación)",
        "was zu tun ist (Liste unten; englische Namen gehen auch)": "qué hay que hacer (lista abajo; los nombres en inglés también valen)",
        "Einstellungen: %s": "Ajustes: %s",
        "Keine Karte gefunden": "No se encontró ninguna tarjeta",
        "Neue Fassung verfügbar": "Nueva versión disponible",
        "Es gibt eine neuere Fassung: %s. Sie benutzen %s.": "Hay una versión más reciente: %s. Usted usa %s.",
        "Zur Download-Seite wechseln?": "¿Abrir la página de descarga?",
        "Es liegt keine JamMan-Karte im Rechner.": "No hay ninguna tarjeta JamMan en este ordenador.",
        "Nimm die microSD aus dem Pedal und stecke sie in einen Kartenleser oder Kartenslot. Über das USB-Kabel am Pedal erscheint sie nicht als Laufwerk.": "Saque la microSD del pedal e insértela en un lector de tarjetas o en una ranura. Con el cable USB puesto en el pedal no aparece como unidad.",
        "Ordner selbst wählen?": "¿Elegir la carpeta usted mismo?",
        "Ordner der Karte wählen": "Elegir la carpeta de la tarjeta",
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
        "vormerken": "reservar",
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
        'DATEIEN':
         '文件',
        'Funktionstest in allen fünf Sprachen':
         '五种语言的自我测试',
        'Dauer und Einstellungen kommen aus Dateigröße und WAV-Kopf — der Ton selbst wird dafür nicht übertragen.':
         '时长和设置只在取回后显示——通过 USB 只读取名称。',
        'Marken außerhalb der Datei wurden verworfen.':
         '文件范围外的标记已丢弃。',
        '%d Teile: %s':
         '%d 段：%s',
        '%d Teile, Schnitt an Pausen gesucht':
         '%d 段，在停顿处寻找切分点',
        '%d Teile, gleichmäßig geschnitten':
         '%d 段，平均切分',
        'in %d Patches geteilt':
         '已分成 %d 个音色',
        'Marken':
         '标记',
        'Teilen':
         '切分',
        'Alle Marken löschen':
         '清除所有标记',
        'Alle Pausen als Marken':
         '将所有停顿设为标记',
        'Pausen gefunden: %d':
         '找到 %d 处停顿',
        'Keine Pause gefunden.':
         '没有找到停顿。',
        'Wellenform nicht lesbar.':
         '无法读取波形。',
        '%s   •   Klick setzt eine Marke, Rechtsklick entfernt sie':
         '%s   •   单击设置标记，右键删除标记',
        'Schwelle %':
         '阈值 %',
        'Pause s':
         '停顿 秒',
        'Fade ms':
         '淡变 毫秒',
        'Marken bitte als Zahlen mit Komma, z. B. 60, 120.':
         '请用逗号分隔的数字填写标记，例如 60, 120。',
        'Schwelle, Pause und Fade bitte als Zahlen eintragen.':
         '阈值、停顿和淡变请填写数字。',
        'Schwelle bitte als Zahl zwischen 1 und 99.':
         '阈值必须是 1 到 99 之间的数字。',
        'Pause bitte als Zahl zwischen 0,01 und 30 Sekunden.':
         '停顿必须是 0.01 到 30 秒之间的数字。',
        'Fade bitte als Zahl zwischen 0 und 50.':
         '淡变必须是 0 到 50 之间的数字。',
        'Es wird an %d gesetzten Marken geschnitten.':
         '在 %d 个标记处切分。',
        'Es wird an allen %d gefundenen Pausen geschnitten.':
         '在所有找到的 %d 处停顿切开。',
        'Schnittpunkt in Sekunden (mehrfach möglich)':
         '切分点（秒，可多次给出）',
        'an jeder gefundenen Pause trennen':
         '在每个找到的停顿处切开',
        'Ruhepegel für die Pausenerkennung in Prozent (Vorgabe 35)':
         '停顿识别的静音电平（默认 35）',
        'kürzeste Pause, an der geschnitten wird (Vorgabe 0,25 s)':
         '可切开的最短停顿（默认 0,25 秒）',
        'Fade an jeder Naht in Millisekunden (Vorgabe 0)':
         '每处接缝的淡变（毫秒，默认 0）',
        'Ende einer Pause':
         '停顿结束',
        'Marke':
         '标记',
        'Herausgeben':
         '导出',
        'Alles herausgeben …':
         '全部导出 …',
        'Wohin herausgeben?':
         '导出到哪里？',
        'Erst eine Karte öffnen — dann läßt sie sich herausgeben.':
         '请先打开存储卡——然后才能导出。',
        'Herausgegeben nach: %s':
         '已导出到：%s',
        '%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv und LIESMICH.txt.':
         '%d 条音轨为 WAV，%d 个位置无音频，另含 karte.csv 和 LIESMICH.txt。',
        'Die Karte wurde nur gelesen. Die Dateien brauchen dieses Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text.':
         '仅读取了存储卡。这些文件不需要本工具：WAV 就是 WAV，清单是纯文本。',
        'Herausgabe einer JamMan-Karte':
         'JamMan 存储卡导出',
        'Datei':
         '文件',
        'Gerät: %s':
         '设备：%s',
        'Aufgenommen: %s':
         '记录时间：%s',
        'Werkzeug: %s':
         '工具：%s',
        'In diesem Ordner liegen %d Tonspuren als gewöhnliche WAV-Dateien, benannt nach ihrem Platz und ihrem Namen in der Karte. %d Plätze hatten keinen Ton.':
         '此文件夹包含 %d 条音轨，均为普通 WAV 文件，按位置和卡片中的名称命名。%d 个位置没有音频。',
        'karte.csv enthält zu jeder Spur: Platz, Name, Datei, Dauer, Stoppart, Wiedergabe, Tempo und Rhythmus. Semikolon als Trennzeichen, damit Excel und LibreOffice sie direkt öffnen.':
         'karte.csv 列出每条音轨：位置、名称、文件、时长、停止方式、播放、速度和节奏。以分号分隔，Excel 和 LibreOffice 可直接打开。',
        'Diese Dateien brauchen das Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text. Wer später ein anderes Gerät oder Programm benutzt, kann damit weiterarbeiten. Die Karte selbst wurde nur gelesen, nicht verändert.':
         '这些文件不需要本工具：WAV 就是 WAV，清单是纯文本。日后改用其他设备或程序也能继续使用。存储卡本身仅被读取，未被修改。',
        'Herausgabe: %d WAV-Dateien gezählt, %d erwartet':
         '导出：计得 %d 个 WAV 文件，预期 %d 个',
        'Herausgabe: karte.csv fehlt':
         '导出：缺少 karte.csv',
        'Herausgabe: %d Zeilen in der Liste, %d erwartet':
         '导出：清单有 %d 行，预期 %d 行',
        'Herausgabe: LIESMICH.txt fehlt':
         '导出：缺少 LIESMICH.txt',
        'Nur-Lesen-Modus: „%s“ ist ein unbekanntes Gerät (%s). Der Kartenaufbau ist nicht belegt, deshalb wird nichts geschrieben. Lesen ist erlaubt (liste). Mit „jamman.py struktur <karte>“ entsteht ein Bericht, den Sie weitergeben können.':
         '只读模式：“%s”是未知设备（%s）。存储卡结构尚未确认，因此不会写入。允许读取（liste）。用“jamman.py struktur <卡>”可生成可转交的报告。',
        'Speicherkarte des %s':
         '%s 的存储卡',
        'Struktur melden …':
         '报告结构 …',
        'Strukturbericht':
         '结构报告',
        'Der Bericht ließ sich nicht schreiben: %s':
         '无法写入报告：%s',
        'Nur-Lesen-Modus: unbekanntes Gerät „%s“. Die Karte wird gelesen; geschrieben wird nichts, weil der Aufbau nicht belegt ist. Mit „Struktur melden …“ können Sie ihn belegen.':
         '只读模式：未知设备“%s”。可以读取存储卡，但不会写入，因为结构尚未确认。请用“报告结构 …”提供证据。',
        'Bericht geschrieben: %s':
         '已写入报告：%s',
        'Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.':
         '包含：文件夹名称和 XML 文件，不含音频，也不含您的文字（音色名称以 … 代替）。共 %d 个 XML 文件。',
        'Bitte schicken Sie diese Datei an mich oder hängen Sie sie an ein GitHub-Issue — dann kann ich den Aufbau prüfen und das Gerät freigeben.':
         '请把此文件发给我，或附在 GitHub issue 上 —— 这样我就能检查结构并支持该设备。',
        'Spalten (%s): %s':
         '列（%s）：%s',
        'Spalten (%s): Aufbau unvollständig':
         '列（%s）：结构不完整',
        'Länge':
         '长度',
        'fade':
         '淡出',
        'loop':
         '循环',
        'single':
         '单次',
        "Fehler: auf '%s' liegt kein unterstützter JamMan-Geräteordner (%s). Das ist keine JamMan-Karte.":
         "  错误：'%s' 中没有受支持的 JamMan 设备文件夹（%s）。这不是 JamMan 存储卡。",
        'beim Start automatisch nach Updates sehen':
         '启动时自动检查更新',
        'nach jedem Update automatisch prüfen':
         '每次更新后自动检查',
        'Aktualisierung':
         '更新',
        'Selbsttest nach Update':
         '更新后自检',
        'Fenster zu schmal für seinen Inhalt: %d von %d Pixel — bitte breiter ziehen':
         '窗口比内容窄：%d / %d 像素 —— 请拉宽',
        'abgeschnittene Beschriftung: %s':
         '文字被截断：%s',
        'Die letzte Sicherung stammt aus dem anderen Speicher. Bitte oben umschalten und es noch einmal versuchen.':
         '上次备份来自另一个存储器。请在上方切换后重试。',
        'deutscher Text in der Anzeige: %s':
         '界面中残留德语文字：%s',
        'sofort':
         '立即',
        '%s   •   %s   •   Klick in die Spur springt dorthin':
         '%s   •   %s   •   点击音轨即可跳转',
        'Per E-Mail senden':
         '通过电子邮件发送',
        'Mail im Mailprogramm vorbereitet — abschicken mußt du selbst.':
         '已在邮件程序中准备好邮件 —— 需要你自己发送。',
        'Mailprogramm ließ sich nicht öffnen.':
         '无法打开邮件程序。',
        'Hier liegen einmal die Zusatzstarter und die Textdateien.\nDer Selbsttest im Werkzeug schreibt seinen Bericht dorthin, wo das Programm liegt: .jamman/.\n':
         '此文件夹用于存放附加启动器和文本文件。\n功能测试会把报告写在程序所在的 .jamman/ 中。\n',
        '  Ordner aufgeräumt — das Werkzeug startet neu …':
         '  文件夹已整理 —— 程序将重新启动 …',
        'Diese Fassung liegt in einer anderen Ordneraufteilung. Bitte einmal von Hand herunterladen:\n%s':
         '此版本使用不同的文件夹结构。请手动下载一次：\n%s',
        'Diese Fassung des Werkzeugs kann sich nicht selbst aktualisieren (neue Ordneraufteilung). Bitte einmal von Hand herunterladen:\n%s':
         '此版本的工具无法自行更新（新的文件夹结构）。请手动下载一次：\n%s',
        'Als Issue vorbereiten':
         '准备为 Issue',
        'Issue im Browser vorbereitet — abschicken mußt du selbst.':
         '已在浏览器中准备好 Issue —— 需要你自己提交。',
        'Browser ließ sich nicht öffnen.':
         '无法打开浏览器。',
        'Nichts wird gesendet, solange du nicht selbst auf GitHub auf Absenden drückst.':
         '除非你自己在 GitHub 上点击提交，否则不会发送任何内容。',
        'Selbsttest':
         '功能测试',
        'Selbsttest …':
         '功能测试 …',
        'Bericht des Selbsttests':
         '功能测试报告',
        'Kopieren':
         '复制',
        'Kopieren ging nicht.':
         '复制失败。',
        'In die Zwischenablage kopiert.':
         '已复制到剪贴板。',
        'Das Werkzeug prüft sich selbst: es legt eine Testkarte an, drückt jeden Knopf und spielt die Dateibefehle durch.\n\nDabei gehen kurz Fenster auf und zu. Das Pedal bleibt unberührt, es wird nichts an deinen Karten geändert.\n\nDauert ein paar Sekunden. Fortfahren?':
         '程序会自我检测：创建测试卡、按下每个按钮并执行文件命令。\n\n期间会短暂打开和关闭窗口。踏板不受影响，你的存储卡不会有任何改动。\n\n需要几秒钟。继续吗？',
        '  [Hinweis] Kein Fenster geprüft (%s): dieses Python hat Tk 8.5 — es lief das Textmenü.':
         '  [提示] 未检查窗口（%s）：此 Python 的 Tk 为 8.5 —— 已运行文本菜单。',
        '  Funktionstest des JamMan Card Tool':
         '  JamMan Card Tool 功能测试',
        '  System:  %s':
         '  系统：%s',
        '  Python:  %s':
         '  Python：%s',
        '  Fassung: %s':
         '  版本：%s',
        '  Testkarte: %s':
         '  测试卡：%s',
        '  [ok]   Befehl %s':
         '  [ok]   命令 %s',
        '  [ok]   Befehl %s meldet: %s':
         '  [ok]   命令 %s 提示：%s',
        '  [FEHL] Befehl %s: %s':
         '  [失败] 命令 %s：%s',
        '  [Hinweis] Ohne Tk kein Fenstertest: %s':
         '  [提示] 没有 Tk 无法测试窗口：%s',
        '  %d Befund(e) — bitte mitschicken (stehen auch in %s).':
         '  发现 %d 个问题 —— 请一并发送（也记录在 %s 中）。',
        '  Ergebnis: keine Befunde.':
         '  结果：未发现问题。',
        '  [FEHL] Fenster (%s): %s':
         '  [失败] 窗口（%s）：%s',
        '  [ok]   Fenster (%s): %d Knöpfe, keine Ausnahme':
         '  [ok]   窗口（%s）：%d 个按钮，无异常',
        '%d Balkenfenster blieben offen':
         '有 %d 个进度窗口未关闭',
        'Fenster':
         '窗口',
        'Abgebrochen — keine Datei geschrieben.':
         '已取消 —— 未写入任何文件。',
        'Es läuft noch eine Arbeit. Abbrechen und das Fenster schließen?\n\nDie unfertige Datei wird gelöscht.':
         '还有任务在运行。要取消并关闭窗口吗？\n\n未完成的文件将被删除。',
        '%d von %d Patches gelesen':
         '已读取 %d / %d 个音色',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe aus dieser Sicherung zurückholen?\n\n  %s\n\nDie Aufnahme bleibt, wie sie ist. Der jetzige Stand wird vorher gesichert.':
         '%d 号音色：从该备份恢复名称、停止方式、节奏和播放方式？\n\n  %s\n\n录音保持不变。当前状态会事先备份。',
        'Patch%d aus dieser Sicherung wiederherstellen?\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen. Der jetzige Stand wird vorher gesichert.':
         '从该备份恢复 %d 号音色？\n\n  %s\n\n例如可以撤销淡出。当前状态会事先备份。',
        'Einstellungen schreiben …':
         '正在写入设置 …',
        'Aufnahme bearbeiten …':
         '正在处理录音 …',
        'fertig':
         '完成',
        'einen Moment …':
         '稍等 …',
        'Sicherung anlegen …':
         '正在创建备份 …',
        'Liste neu lesen …':
         '正在重新读取列表 …',
        '  Keine Eingabe möglich — Ende.':
         '  无法输入 —— 结束。',
        'Sicherungen löschen':
         '删除备份',
        '%d von %d gelöscht':
         '已删除 %d / %d',
        '%d von %d gelöscht: %s':
         '已删除 %d / %d：%s',
        'Alle normieren':
         '全部标准化',
        '%d von %d Patches: %s':
         '%d / %d 个音色：%s',
        'Sicherung anlegen':
         '创建备份',
        '%d von %d Dateien · %.0f MB':
         '%d / %d 个文件 · %.0f MB',
        'Doppelklick spielt · Ziehen ordnet um · ⌘ zwei markieren, dann Tauschen · Esc hält an · ⌘O Karte, ⌘S Sicherungen, ⌘L Lücken':
         '双击播放 · 拖动排序 · ⌘ 选两个后交换 · Esc 停止 · ⌘O 存储卡，⌘S 备份，⌘L 空位',
        'Setlist-Dateien':
         'Setlist 文件',
        'Anpassen':
         '调整',
        'Erst eine Datei markieren.':
         '请先选择一个文件。',
        '%d Datei(en) angepasst, %d Zeile(n) umgeschrieben.':
         '已调整 %d 个文件，改写 %d 行。',
        'In diesem Ordner liegen keine Setlist-Dateien:\n%s\n\nBitte einen anderen Ordner wählen.':
         '此文件夹中没有 Setlist 文件：\n%s\n\n请选择其他文件夹。',
        'Alle sind markiert — einzelne mit Klick abwählen.':
         '已全部选中 —— 单击可取消选择。',
        'betrifft Platz: %s':
         '涉及位置：%s',
        'Pegeländerung zurücknehmen':
         '撤销电平改动',
        'Es gibt noch keine Pegelprotokolle.':
         '还没有电平记录。',
        'Pegelprotokolle':
         '电平记录',
        'Welche Änderung soll zurückgenommen werden?':
         '要撤销哪一次改动？',
        'Erst ein Protokoll markieren.':
         '请先选择一个记录。',
        'Im Protokoll stehen keine Einträge.':
         '记录中没有条目。',
        'Ein Patch (Platz %s)':
         '一个音色（位置 %s）',
        '%d Patches':
         '%d 个音色',
        '%s aus diesem Protokoll werden umgekehrt:\n\n%s\n\nNur diese Patches werden vorher gesichert. Fortfahren?':
         '将反转此记录中的 %s：\n\n%s\n\n只备份这些音色。继续吗？',
        'Pegeländerung zurücknehmen …':
         '撤销电平改动 …',
        '  Trockenlauf — nichts geändert.':
         '  试运行 —— 未做改动。',
        '  Jetziger Stand gesichert: %s':
         '  当前状态已备份：%s',
        '  Einstellungen von Patch%d zurückgeholt.':
         '  已恢复 %d 号音色的设置。',
        'Patch%d: Name, Stoppart, Rhythmus und Wiedergabe kommen aus dieser Sicherung zurück:\n\n  %s\n\nDie Aufnahme bleibt unberührt — sie war in dieser Sicherung nicht enthalten.\nDer jetzige Stand wird vorher gesichert.':
         '%d 号音色：名称、停止方式、节奏和播放方式将从该备份恢复：\n\n  %s\n\n录音不受影响 —— 该备份中不含录音。\n当前状态会事先备份。',
        'Patch%d wird aus dieser Sicherung wiederhergestellt:\n\n  %s\n\nDamit lässt sich zum Beispiel ein Fade zurücknehmen.\nDer jetzige Stand wird vorher gesichert.':
         '%d 号音色将从该备份恢复：\n\n  %s\n\n例如可以撤销淡出。\n当前状态会事先备份。',
        'Patch%d zurückgeholt':
         '已恢复 %d 号音色',
        'Alle Patches werden auf gleiche Lautheit gebracht:\n\n  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\nPatches, die schon passen, bleiben unangetastet. Nur die\nübrigen werden vorher gesichert und neu geschrieben.\n\nFortfahren?':
         '所有音色统一到相同响度：\n\n  目标 −18 dB 响度，峰值最高 −1 dB\n\n已经合适的音色不改动。只对其余音色先备份再重写。\n\n继续吗？',
        '  Nichts zu tun: alle Patches liegen bereits auf der Ziel-Lautheit.':
         '  无需处理：所有音色都已达到目标响度。',
        '  Ein Patch war schon auf Ziel und blieb unverändert.':
         '  有一个音色已在目标电平，未改动。',
        '  Schon auf Ziel: %d von %d Patches — unverändert gelassen.':
         '  已在目标电平：%d / %d 个音色 —— 未改动。',
        '  %d Patches waren schon auf Ziel und blieben unverändert.':
         '  %d 个音色已在目标电平，未改动。',
        'Blättern':
         '翻页',
        'Normierung zurücknehmen …':
         '撤销标准化 …',
        'auf −1 dB Spitze normalisieren':
         '标准化到 −1 dB 峰值',
        'Anhebung höchstens auf 0 dB':
         '提升最多到 0 dB',
        'Sicherungen verwalten …':
         '管理备份 …',
        'Lücken bei den Plätzen %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         '位置 %s%s 处有空缺 —— “补齐空位”会把后面的位置前移。',
        'Zurücknehmen (letzter Schritt)':
         '撤销（上一步）',
        'Zurücknehmen':
         '撤销',
        'ältere markieren':
         '标记更早的',
        'Anderer Ordner …':
         '其他文件夹 …',
        'Neue Sicherung anlegen (alles sichern)':
         '新建备份（全部）',
        'Sicherungen …':
         '备份 …',
        'Ordner mit den Sicherungen':
         '存放备份的文件夹',
        'Sammelsicherung':
         '救援备份',
        'Rette':
         '正在抢救',
        'Es gibt keine unersetzlichen Patches.':
         '没有不可替代的音色。',
        '%d Patches wurden nach %s kopiert. Die Zuordnung steht dort in Rettung.txt. Jetzt lassen sich die alten Sicherungen löschen.':
         '%d 个音色已复制到 %s。对应关系见其中的 Rettung.txt。现在可以删除旧的备份了。',
        'Nur die unersetzlichen Patches retten':
         '只抢救不可替代的音色',
        '⟨%d Patch nur hier⟩':
         '⟨仅此处有 %d 个音色⟩',
        '%d markiert · %.1f MB':
         '已标记 %d 项 · %.1f MB',
        'nichts markiert':
         '未标记任何项',
        'Vorgabe: neueste 3 und letzte 7 Tage behalten':
         '默认：保留最新 3 个和最近 7 天',
        'Fertig. Ordner:\n%s':
         '完成。文件夹：\n%s',
        'Alte Sicherungen in diesem Ordner ansehen und löschen?':
         '是否查看此文件夹中的旧备份并删除？',
        'Lücke bei Platz %s%s — „Lücken schließen“ rückt die folgenden Plätze nach vorn.':
         '位置 %s%s 处有空缺 —— “补齐空位”会把后面的位置前移。',
        'Platz %s ist nicht belegt — die Liste hat eine Lücke.':
         '位置 %s 未占用 —— 列表中有空缺。',
        'Lücken schließen':
         '补齐空位',
        'Patch%d → Patch%d':
         'Patch%d → Patch%d',
        '%d Patches rücken nach vorn: %s%s\n\nDie Reihenfolge bleibt, nur die Platznummern ändern sich. Die Zuordnung wird vorher gesichert. Setlist-Dateien, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d 个音色前移：%s%s\n\n顺序不变，只有位置编号改变。对应关系会事先保存。引用位置编号的 Setlist 文件之后将不再匹配。继续吗？',
        'Aufgerückt: %d Patches':
         '已前移 %d 个音色',
        'Sollen vorhandene Setlist-Dateien auf die neuen Platznummern umgeschrieben werden?':
         '是否要把现有的 Setlist 文件改写为新的位置编号？',
        'Ordner mit Setlist-Dateien':
         '含 Setlist 文件的文件夹',
        'Setlists angepaßt: %d Datei(en)':
         'Setlist 已调整：%d 个文件',
        'Fehler: Platz %d ist belegt (%s).':
         '错误：位置 %d 已被占用（%s）。',
        'Beenden':
         '退出',
        'Dem Python fehlen die Zertifikate. Zwei Wege: im Python-Ordner „Install Certificates.command“ ausführen — oder „pip install certifi“ — oder die Datei von der Release-Seite von Hand laden.':
         'Python 缺少证书。两个办法：在 Python 文件夹里运行 “Install Certificates.command”，或执行 “pip install certifi” —— 也可以从发布页手动下载文件。',
        'Achtung: das Zertifikat ließ sich nicht prüfen — die Datei wurde ohne Prüfung geladen.':
         '注意：无法验证证书 —— 文件是在未校验的情况下下载的。',
        'Die Datei ist unvollständig: %d statt %d Bytes.':
         '文件不完整：%d 字节，而不是 %d 字节。',
        'Neue Fassung %s wird geholt …':
         '正在获取 %s …',
        'Fassung %s ist installiert. Bitte das Fenster schließen und neu starten.':
         '已安装 %s。请关闭窗口并重新启动。',
        'Update beim Start':
         '启动时更新',
        'Die Karte ist eingebunden, aber das Dateisystem liefert\nLesefehler:\n\n%s\n\nDas liegt an der Karte oder am Einhängepunkt, nicht am\nWerkzeug.\n\nErste Schritte:\n  - Karte im JamMan einschalten (das Pedal prüft sie)\n  - dmesg | tail -20\n  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n    bzw. sudo exfatfsck <gerät>':
         '卡片已挂载，但文件系统报告读取错误：\n\n%s\n\n问题在卡片或挂载点，不在工具。\n\n第一步：\n  - 把卡片插入 JamMan 并开机（踏板会检查卡片）\n  - dmesg | tail -20\n  - 卸载并检查：sudo fsck.vfat -n <设备>\n    或 sudo exfatfsck <设备>',
        'kein Pedal':
         '没有踏板',
        'Beschreibung …':
         '描述…',
        'Rückwärts: ohne ffmpeg keine Hörprobe — das Pedal spielt es trotzdem rückwärts.':
         '倒放：没有 ffmpeg 无法试听——踏板仍会倒放。',
        'Tempo':
         '速度',
        'Suchen …':
         '搜索…',
        'Text im Namen (leer lassen = alles zeigen):':
         '名称中的文字（留空 = 全部显示）：',
        'Erst eine Karte oder eine geholte Kopie öffnen — dann läßt sich suchen.':
         '请先打开卡片或已获取的副本，然后才能搜索。',
        'Suchen':
         '搜索',
        'Suche fehlgeschlagen: %s':
         '搜索失败：%s',
        'Suchen:':
         '搜索：',
        'Karte:':
         '卡片：',
        'Meldungen stehen im Fenster; Fehler zusätzlich in jamman-fehler.log.':
         '消息显示在窗口中；错误也会写入 jamman-fehler.log。',
        'Firmware-Dateien jetzt ins Pedal schreiben? Ein Abbruch mittendrin kann das Pedal unbrauchbar machen. [ja/nein] ':
         '现在把固件文件写入踏板？中途中断可能使踏板无法使用。[是/否] ',
        'Zielordner für die Firmware':
         '固件目标文件夹',
        'Ordner mit der geholten Firmware':
         '已获取固件的文件夹',
        'In diesem Ordner liegt keine Firmware (usr/JamManUI, usr/loopengine …).':
         '此文件夹中没有固件（usr/JamManUI、usr/loopengine…）。',
        'Firmware übernehmen':
         '应用固件',
        'Firmware übernehmen …':
         '应用固件…',
        'Firmware dieses Pedals holen …':
         '获取此踏板的固件…',
        '%s kam unvollständig (%d von %d Bytes).':
         '%s 不完整（%d/%d 字节）。',
        '%d Firmware-Dateien werden jetzt in das Pedal geschrieben. Vorher wird der jetzige Stand gesichert.\n\nEin Abbruch mittendrin kann das Pedal unbrauchbar machen — es gibt keinen Weg zurück.\n\nWirklich schreiben?':
         '现在将把 %d 个固件文件写入踏板。会先备份当前状态。\n\n中途中断可能使踏板无法使用——无法恢复。\n\n确定写入？',
        'Sichere den jetzigen Stand …':
         '正在备份当前状态…',
        'Gesichert: %d Dateien nach %s':
         '已备份：%d 个文件到 %s',
        'Firmware geholt: %d Dateien':
         '已获取固件：%d 个文件',
        'Firmware geholt:\n%s\n%d Dateien':
         '已获取固件：\n%s\n%d 个文件',
        'Fertig: %d Dateien geschrieben':
         '完成：已写入 %d 个文件',
        'Nicht bestätigt: %d':
         '未确认：%d',
        'Firmware geschrieben: %d Dateien\nSicherung: %s\nJetzt das Pedal aus- und wieder einschalten.':
         '固件已写入：%d 个文件\n备份：%s\n现在请关闭踏板再打开。',
        'Schreibe':
         '正在写入',
        'Hole':
         '正在获取',
        'JamList aus JamManager XT holen …':
         '从 JamManager XT 获取 JamList…',
        'Setlist als JamList ablegen …':
         '将 setlist 保存为 JamList…',
        'Setlist speichern':
         '保存 setlist',
        'Bibliothek von JamManager XT':
         'JamManager XT 库',
        'JamList wählen':
         '选择 JamList',
        'In dieser Bibliothek liegt keine JamList.':
         '此库中没有 JamList。',
        'Geholt: %s\n%d Plätze\n%s':
         '已获取：%s\n%d 个位置\n%s',
        'JamList ablegen':
         '保存 JamList',
        '%s%s%s als neue JamList eintragen? Die Bibliothek wird dabei verändert — vorher eine Kopie anlegen.':
         '将 %s%s%s 添加为新的 JamList？库将被修改——请先备份。',
        'JamList %s angelegt (id %d, %d Plätze)':
         '已创建 JamList %s（id %d，%d 个位置）',
        'Keine JamList mit dem Namen %s.':
         '没有名为 %s 的 JamList。',
        'belegte Plätze':
         '已用位置',
        'Geschrieben: %s (%d Plätze)':
         '已写入：%s（%d 个位置）',
        'Übernehmen':
         '应用',
        'Speicherabbild':
         '内存映像',
        'Speicherabbild speichern':
         '保存内存映像',
        'Speicherabbild aufnehmen …':
         '生成内存映像…',
        'Speicherabbilder vergleichen …':
         '比较内存映像…',
        'Speicherabbilder vergleichen':
         '比较内存映像',
        'Erstes Speicherabbild':
         '第一个内存映像',
        'Zweites Speicherabbild':
         '第二个内存映像',
        'Nur im ersten Bild: %d':
         '仅在第一个映像中：%d',
        'Nur im zweiten Bild: %d':
         '仅在第二个映像中：%d',
        'Gleicher Pfad, andere Größe: %d':
         '路径相同、大小不同：%d',
        'Die beiden Bilder sind gleich.':
         '两个映像相同。',
        'Erstes:':
         '第一个：',
        'Zweites:':
         '第二个：',
        'Aufruf:':
         '用法：',
        'Lese den Verzeichnisbaum …':
         '正在读取目录树…',
        'Speicherabbild: %d Einträge':
         '内存映像：%d 项',
        'Speicherabbild geschrieben:\n%s':
         '内存映像已保存：\n%s',
        'Speicherabbild geschrieben: %d Einträge nach %s':
         '内存映像已保存：%d 项到 %s',
        'Dateien für „speicherabbild“ und „vergleich“':
         '用于“speicherabbild”和“vergleich”的文件',
        'Sortierung':
         '排序',
        '%d von %d Patches passen zu „%s“.':
         '%d/%d 个音色匹配“%s”。',
        'Beschreibung':
         '描述',
        'Interpret':
         '艺人',
        'Genre':
         '风格',
        'Stichwörter':
         '标签',
        'Bewertung':
         '评级',
        'Tempo (BPM)':
         '速度（BPM）',
        'Die Datei %s fehlt.':
         '文件 %s 不存在。',
        'Setlists anpassen':
         '调整 setlist',
        '%d Setlist-Dateien in %s an die neuen Plätze anpassen? Jede Datei wird vorher daneben gesichert.':
         '将 %s 中的 %d 个 setlist 文件调整为新的位置？每个文件都会先备份在旁边。',
        'Keine Setlist nannte einen bewegten Platz.':
         '没有 setlist 提到被移动的位置。',
        'Setlists nicht angepaßt: ':
         'setlist 未调整：',
        'Setlists angepaßt: ':
         'setlist 已调整：',
        'Plätze aufrücken':
         '填补空缺',
        'Plätze aufrücken …':
         '填补空缺…',
        'Es gibt keine Lücken — die Plätze sind schon lückenlos belegt.':
         '没有空缺——位置已经是连续的。',
        '%d Patches rücken in %s nach vorn: %s%s\n\nJeder bewegte Platz wird vorher auf den Rechner gesichert. Setlists im Pedal, die auf Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?':
         '%d 个音色在%s中前移：%s%s\n\n每个移动的位置都会先备份到电脑。踏板中按位置编号的 setlist 将不再匹配。继续吗？',
        'Rücke auf':
         '正在前移',
        'Fertig: %d Plätze aufgerückt, %d vorher gesichert':
         '完成：前移 %d 个位置，先备份 %d 个',
        'Aufgerückt: %d Plätze':
         '已前移：%d 个位置',
        'Kopie auf dem Rechner — Änderungen hier wirken nicht auf das Pedal.':
         '电脑上的副本——此处的更改不会影响踏板。',
        'Patch%d wird aus der Kopie auf dem Rechner entfernt. Das Pedal bleibt unverändert. Fortfahren?':
         '将从电脑上的副本中删除 Patch%d。踏板保持不变。继续吗？',
        'Patch%d wird von der Karte entfernt. Vorher wird er auf dem Rechner gesichert. Fortfahren?':
         '将从卡片中删除 Patch%d。会先备份到电脑。继续吗？',
        'Ja':
         '是',
        'Nein':
         '否',
        'Dem Python fehlen die Zertifikate. Auf macOS hilft „Install Certificates.command“ im Python-Ordner, oder: pip install certifi':
         'Python 缺少证书。在 macOS 上请运行 Python 文件夹中的“Install Certificates.command”，或：pip install certifi',
        'GitHub ist nicht erreichbar: %s':
         '无法访问 GitHub：%s',
        'unbekannter Grund':
         '原因未知',
        'Gelöscht: %s':
         '已删除：%s',
        'Fehler:':
         '错误：',
        'Platz löschen':
         '删除位置',
        'Welcher Platz soll gelöscht werden? (z. B. 30 oder Patch30)':
         '要删除哪个位置？（例如 30 或 Patch30）',
        'Platz löschen …':
         '删除位置…',
        '%s ist nicht belegt — nichts zu löschen.':
         '%s 未被占用——无可删除。',
        '%s wird aus %s gelöscht. Das läßt sich nicht rückgängig machen. Fortfahren?':
         '将从%s删除%s。此操作无法撤销。继续吗？',
        'Lösche':
         '正在删除',
        'Gelöscht:':
         '已删除：',
        'Fehler beim Löschen:':
         '删除时出错：',
        'Die Datei ist %s lang — das Pedal nimmt höchstens 10 Minuten je Loop an. Bitte im Hauptfenster kürzen oder teilen.':
         '文件长度为 %s——踏板每个循环最多接受 10 分钟。请在主窗口中缩短或分割。',
        'Dieser Ordner ist nicht lesbar: %s':
         '无法读取此文件夹：%s',
        'Auswahl:':
         '所选：',
        'Pegelprotokoll wählen':
         '选择电平日志',
        'Audiodatei zum Teilen auswählen':
         '选择要分割的音频文件',
        'Dateiname:':
         '文件名：',
        'Speichern':
         '保存',
        'Bitte einen Dateinamen angeben.':
         '请输入文件名。',
        'Überschreiben':
         '覆盖',
        '%s gibt es schon. Ersetzen?':
         '%s 已存在。要替换吗？',
        'Speicherkarte des JamMan wählen':
         '选择 JamMan 卡片',
        'Zielordner für die Sicherung':
         '备份的目标文件夹',
        'Dateien':
         '文件',
        'Nach oben':
         '上一级',
        'Öffnen':
         '打开',
        'Bitte eine Datei auswählen.':
         '请选择一个文件。',
        'Alle Dateien':
         '所有文件',
        'Die Datei konnte nicht geschrieben werden: %s':
         '无法写入文件：%s',
        'ohne':
         '无',
        'ende':
         '结尾',
        'klick':
         '节拍',
        '%d Patches ohne Ton — nur die Beschreibung ist geholt.':
         '%d 个音色没有音频——只获取了描述。',
        'Ganzen Ordner aufs Pedal …':
         '整个文件夹写到踏板…',
        'Ordner mit Patch-Ordnern wählen':
         '选择包含音色文件夹的目录',
        'Ganzen Ordner aufs Pedal':
         '整个文件夹写到踏板',
        '%d Patch-Ordner werden in %s geschrieben. Belegte Plätze werden vorher gesichert, unveränderte übersprungen. Fortfahren?':
         '将把 %d 个音色文件夹写入%s。已占用的位置会先备份，未更改的会跳过。继续吗？',
        'internen Speicher':
         '内部存储',
        'der Karte':
         '卡片',
        'Im Ordner liegen keine Patch-Ordner (erwartet: Patch01 … Patch200).':
         '该目录中没有音色文件夹（应为 Patch01 … Patch200）。',
        'Fertig: %d Patches geschrieben, %d übersprungen, %d vorher gesichert':
         '完成：写入 %d 个音色，跳过 %d 个，先备份 %d 个',
        'Übertragen: %d Dateien · unverändert übersprungen: %d':
         '已传输 %d 个文件 · 跳过未变更的 %d 个',
        'Zielplatz auf dem Pedal (z. B. 17 oder Patch17):':
         '踏板上的目标位置（例如 17 或 Patch17）：',
        'Unerwarteter Fehler':
         '意外错误',
        'Das Werkzeug wurde unerwartet beendet.':
         '工具意外停止。',
        'Protokoll:':
         '日志：',
        'Auswahl kopieren':
         '复制所选',
        'Nichts markiert — bitte zuerst Text markieren.':
         '未选中任何内容——请先选中文本。',
        'Anleitung speichern':
         '保存说明',
        'Gespeichert.':
         '已保存。',
        'Alles kopieren':
         '全部复制',
        'Als Datei speichern':
         '另存为文件',
        'Anleitung':
         '说明',
        'Zum Umwandeln fehlt ffmpeg.':
         '转换需要 ffmpeg，但未安装。',
        'Die Programmdateien lassen sich hier nicht ersetzen: %s':
         '无法在此替换程序文件：%s',
        'Unerwarteter Fehler: %s':
         '意外错误：%s',
        'Aktualisieren':
         '更新',
        'Die neueste Fassung wird von GitHub geladen und ersetzt die Programmdateien. Ihre Einstellungen und Ihre Karte bleiben unberührt. Fortfahren?':
         '将从 GitHub 下载最新版本并替换程序文件。您的设置和卡片不受影响。继续吗？',
        'Jetzt aktualisieren?':
         '现在更新？',
        'Aktualisiert':
         '已更新',
        'Nicht aktualisiert':
         '未更新',
        'Fenster jetzt schließen?':
         '现在关闭窗口？',
        'GitHub ist nicht erreichbar.':
         '无法访问 GitHub。',
        'Sie benutzen bereits die neueste Fassung (%s).':
         '您已在使用最新版本（%s）。',
        'Zur Fassung %s gibt es keine Datei zum Herunterladen.':
         '版本 %s 没有可下载的文件。',
        'Lade Fassung %s …':
         '正在下载版本 %s …',
        'Herunterladen fehlgeschlagen: %s':
         '下载失败：%s',
        'Die heruntergeladene Datei ist kein gültiges ZIP.':
         '下载的文件不是有效的 ZIP。',
        'Die Datei ist beschädigt (%s).':
         '文件已损坏（%s）。',
        'In der Datei fehlt jamman.py.':
         '文件中缺少 jamman.py。',
        'Die Datei gehört nicht zur Fassung %s.':
         '该文件不属于版本 %s。',
        'Fassung %s ist installiert (%d Dateien). Bitte das Fenster schließen und neu starten.':
         '版本 %s 已安装（%d 个文件）。请关闭窗口并重新启动。',
        'Die Karte hängt nicht im Rechner — nichts auszuwerfen. Steckt sie im Pedal, führt das Pedal sie nicht als Laufwerk.':
         '卡片不在电脑中——没有可弹出的内容。若卡片在踏板里，踏板不会把它当作驱动器。',
        'Auswerfen ging nicht. Bitte im Dateimanager auf „Auswerfen" klicken.':
         '弹出失败。请在文件管理器中使用“弹出”。',
        'Keine Karte im Rechner gefunden.':
         '在电脑中未找到卡片。',
        'Karte im Rechner:  „Karte wählen“ drücken.':
         '卡片在电脑里：按“选择卡片”。',
        'Karte im Pedal:    oben rechts „Pedal über USB“ — dort sind Karte und interner Speicher erreichbar.':
         '卡片在踏板里：右上角“通过 USB 连接踏板”——在那里可以访问卡片和内部存储。',
        'Speicherkarte': '存储卡',
        'Kopie vom Pedal': '来自踏板的副本',
        'Speicherkarte des JamMan Stereo': 'JamMan Stereo 的存储卡',
        'Im Pedal steckt keine Karte.': '踏板中没有卡片。',
        'Karte einlegen und dann „Karte wählen“ oder „Neu laden“ drücken.':
         '请插入卡片，然后按“选择卡片”或“重新载入”。',
        'keine Karte':
         '无卡片',
        'Dies ist eine Kopie vom Pedal, nicht die Karte selbst. Über „Karte wählen“ die Karte öffnen.':
         '这是从踏板取回的副本，不是卡片本身。请用“选择卡片”打开卡片。',
        'JamMan Card Tool': 'JamMan Card Tool',
        'Karte und interner Speicher': '卡与内部存储',
        '(für Rückgängig nötig)':
         '（撤销需要）',
        'Darunter ist die Sicherung für Rückgängig. Danach läßt sich der letzte Schreibvorgang nicht mehr zurücknehmen. Trotzdem löschen?':
         '其中包括撤销所需的备份。之后将无法撤销上一次写入。仍要删除吗？',
        'Sicherungen aufräumen …':
         '清理备份……',
        'Sicherungen aufräumen':
         '清理备份',
        'Sicherungen im Zielordner':
         '目标文件夹中的备份',
        '%d Sicherungen · %.1f MB':
         '%d 个备份 · %.1f MB',
        'Neueste behalten:':
         '保留最新：',
        'Markierte löschen':
         '删除所选',
        'Erst Sicherungen in der Liste markieren.':
         '请先在列表中标记备份。',
        '%d Sicherungen mit %.1f MB werden endgültig gelöscht. Fortsetzen?':
         '将永久删除 %d 个备份（%.1f MB）。继续？',
        '%d Sicherungen konnten nicht gelöscht werden.':
         '有 %d 个备份无法删除。',
        'Der Zielordner ist noch nicht angelegt.':
         '目标文件夹尚未创建。',
        'Im Zielordner liegen keine Sicherungen.':
         '目标文件夹中没有备份。',
        'Alle auswählen':
         '全选',
        'Nichts markiert. Alle %d Patches in die Setlist?':
         '未选择任何项目。把所有 %d 个 Patch 放入 Setlist？',
        'Warte auf das Ende des laufenden Vorgangs …':
         '正在等待当前操作结束……',
        'Der Vorgang reagiert nicht — Fenster schließen und neu öffnen.':
         '操作无响应——请关闭窗口后重新打开。',
        'Hole Beschreibungen': '正在读取描述',
        'Es wurde kein Zielplatz eingegeben.':
         '未输入目标位置。',
        'Der Zielplatz muß wie Patch01 … Patch200 heißen, nicht „%s“.':
         '目标位置必须命名为 Patch01 … Patch200，而不是“%s”。',
        'OK': '确定',
        'Ein Vorgang läuft noch — bitte abwarten.':
         '仍有操作在进行——请稍候。',
        'Verbindung neu aufbauen':
         '重新连接',
        'Verbindung wird neu aufgebaut …':
         '正在重新连接……',
        'Lesen und Schreiben über USB. Vor jedem Überschreiben wird der alte Platz auf den Rechner gesichert.':
         '通过 USB 读写。覆盖前会先把原位置保存到电脑。',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller. Schreiben verändert die Karte — vorher wird gesichert.':
         '通过 USB 的速率约为 1,3 MB/s。整张卡用读卡器快得多。写入会修改卡片——之前会先备份。',
        'Audio in den Speicher schreiben …': '把音频写入存储器……',
        'Beim JamMan Solo XT behält der FS3X seine Grundfunktionen. Eine eigene Belegung speichert das Pedal zwar, wertet sie aber nicht aus — deshalb wird sie hier nicht angeboten.':
         '在 JamMan Solo XT 上，FS3X 保留其基本功能。踏板虽会保存自定义分配，但不会执行，因此这里不提供。',
        'Änderungen wirken nach einem Neustart des Pedals.': '更改在踏板重启后生效。',
        'gewünscht': '请求值',
        'Pedal meldet': '踏板返回',
        '(nicht belegt)':
         '（未分配）',
        'UndoRedo = Rückgängig/Wiederholen · Reverse = Rückwärts · Tempo = Tempo antippen':
         'UndoRedo = 撤销/重做 · Reverse = 倒放 · Tempo = 打拍子',
        'Sichern': '保存',
        'Schreiben': '写入',
        'Setlists': 'Setlist',
        'Pedal': '踏板',
        'Ins Hauptfenster holen …': '取到主窗口……',
        'Einzelnen Patch sichern …': '保存单个 Patch……',
        'Patch-Ordner aufs Pedal …': 'Patch 文件夹写入踏板……',
        'Übernommen.': '已应用。',
        'Pedal spielt jetzt:': '踏板现在播放：',
        'Audio aufs Pedal …':
         '音频写入踏板……',
        'Audio aufs Pedal':
         '音频写入踏板',
        'Audiodatei wählen':
         '选择音频文件',
        'Audio':
         '音频',
        'Sichere den alten Platz …':
         '正在备份原位置……',
        'Wandle die Datei …':
         '正在转换文件……',
        'Umwandeln fehlgeschlagen — die Datei muß 44,1 kHz, 16 Bit, Stereo werden.':
         '转换失败——文件必须是 44,1 kHz、16 位、立体声。',
        'Leere den Platz …': '正在清空该位置……',
        'Pedal spielt jetzt Bank/Patch:': '踏板现在播放 Bank/Patch：',
        'Karte erkannt:': '检测到卡：',
        'Als ZIP sichern …':
         '保存为 ZIP……',
        'Als ZIP sichern':
         '保存为 ZIP',
        'Sichere als ZIP …':
         '正在保存为 ZIP……',
        'Sichere:':
         '正在保存：',
        'Fehler beim Sichern:':
         '保存时出错：',
        'Die Setlist ist leer.':
         'Setlist 为空。',
        'Erst Patches in der Liste markieren (⌘-Klick).':
         '请先在列表中标记 Patch（⌘ 单击）。',
        'Es gibt nichts rückgängig zu machen.':
         '没有可撤销的操作。',
        'Fußschalter …':
         '脚踏开关……',
        'Fußschalter':
         '脚踏开关',
        'Tastenbelegung des Pedals':
         '踏板的脚踏开关分配',
        'Werte sind Geräteeinstellungen — nur ändern, wenn du weißt, was sie bedeuten.':
         '这是设备设置——只在你知道含义时才修改。',
        'JamMan':
         'JamMan',
        'Karte':
         '卡',
        'intern':
         '内部',
        'Liste als Textdatei sichern':
         '将列表保存为文本文件',
        'Liste als Textdatei …':
         '列表保存为文本文件……',
        'Mitlaufen':
         '跟随',
        'Mitlaufen an — am Pedal einen Patch wählen.':
         '跟随已开启——在踏板上选择一个 Patch。',
        'Mitlaufen aus.':
         '跟随已关闭。',
        'Rückgängig':
         '撤销',
        'Setlist holen …':
         '获取 Setlist……',
        'Setlist laden':
         '加载 Setlist',
        'Setlist sichern …':
         '保存 Setlist……',
        'Setlist sichern':
         '保存 Setlist',
        'Setlist.txt':
         'Setlist.txt',
        'Stelle wieder her:':
         '正在恢复：',
        '%s wird durch die Sicherung ersetzt. Fortsetzen?':
         '%s 将被备份替换。继续？',
        'Ordner des Patches wählen':
         '选择 Patch 文件夹',
        'Aufs Pedal schreiben':
         '写入踏板',
        '%s ist schon belegt. Der Patch wird vorher auf den Rechner gesichert und dann überschrieben. Fortsetzen?':
         '%s 已被占用。该 Patch 会先保存到电脑，然后被覆盖。继续？',
        'Schreibe aufs Pedal …':
         '正在写入踏板……',
        'Schreibe:':
         '正在写入：',
        'Fehler beim Schreiben:':
         '写入错误：',
        'Patch aufs Pedal schreiben …':
         '把 Patch 写入踏板……',
        '(Vorschlag)': '（建议）',
        'Ziel:': '目标：',
        'noch nicht gewählt': '尚未选择',
        'Ziel ändern …': '更改目标……',
        'Aufräumen':
         '清理',
        'Die angefangene Kopie entfernen? Nur die gerade geholten Dateien werden gelöscht, der Ordner bleibt.':
         '删除未完成的副本？只删除刚刚取来的文件，文件夹保留。',
        'Aufgeräumt:':
         '已清理：',
        'Hole Beschreibungen …': '正在读取描述……',
        'Wird abgebrochen …': '正在取消……',
        'Über USB fließen etwa 1,3 MB/s. Für eine ganze Karte ist ein Kartenleser viel schneller — steckt die Karte im Pedal, bleibt nur dieser Weg.':
         '通过 USB 的速率约为 1,3 MB/s。整张卡用读卡器快得多——如果卡在踏板里，只能走这条路。',
        'Abbrechen': '取消',
        'Abgebrochen.': '已取消。',
        'Fehler beim Öffnen:': '打开时出错：',
        'Alle ins Hauptfenster holen …': '全部取到主窗口……',
        'Zielordner wählen': '选择目标文件夹',
        'Hole Patch': '正在读取 Patch',
        'Fertig:': '完成：',
        'Im Hauptfenster öffnen?': '在主窗口中打开？',
        'Gesichert': '已保存',
        'Pedal über USB': '通过 USB 连接踏板',
        'Das USB-Modul fehlt: greenglass.py muß neben jamman.py liegen.': '缺少 USB 模块：greenglass.py 必须与 jamman.py 放在同一目录。',
        'Gesichert nach:': '已保存到：',
        'Interner Speicher': '内部存储',
        'SD-Karte': 'SD 卡',
        'Kein JamMan gefunden — Pedal einschalten und Kabel prüfen.': '未找到 JamMan——请打开踏板并检查数据线。',
        '(ohne Namen)': '（无名称）',
        'Fehler beim Lesen:': '读取错误：',
        'Erst „Neu laden“ drücken.': '请先按「重新加载」。',
        'Lädt …': '正在加载……',
        'Nur lesen: das Pedal wird nicht verändert.': '只读：不会修改踏板。',
        'Ordner zum Sichern wählen': '选择保存文件夹',
        'Patch sichern …': '保存 Patch……',
        'Verbinde mit dem Pedal …': '正在连接踏板……',
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
        "BEFEHL": "命令",
        "  Auswahl: ": "  选择：",
        "  Anderer Pfad (leer = beenden): ": "  其他路径（留空 = 退出）：",
        "  Patch-Nummer: ": "  音色编号：",
        "  Name (leer = Dateiname): ": "  名称（留空 = 文件名）：",
        "  Stopp (fade/instant, leer = wie Vorlage): ": "  停止方式（fade/instant，留空 = 同模板）：",
        "  Wiedergabe (loop/single, leer = wie Vorlage): ": "  播放方式（loop/single，留空 = 同模板）：",
        "  Pfad zur Audiodatei: ": "  音频文件路径：",
        "  Stopp (fade/instant, leer = lassen): ": "  停止方式（fade/instant，留空 = 保持）：",
        "  Wiedergabe (loop/single, leer = lassen): ": "  播放方式（loop/single，留空 = 保持）：",
        "  Neuer Name (leer = lassen): ": "  新名称（留空 = 保持）：",
        "  Wirklich schreiben? (j/n): ": "  真的要写入吗？（y/n）：",
        "  Welcher Patch: ": "  哪个音色：",
        "  An welche Stelle: ": "  放到哪个位置：",
        "  Wirklich verschieben? (j/n): ": "  真的要移动吗？（y/n）：",
        "  Zielordner (leer = ~/JamMan-Sicherung): ": "  目标文件夹（留空 = ~/JamMan-Sicherung）：",
        "  Wirklich löschen? (j/n): ": "  真的要删除吗？（y/n）：",
        "Kein Abspielprogramm gefunden (aplay, paplay, mpv oder ffplay).": "未找到播放程序（aplay、paplay、mpv 或 ffplay）。",
        "Fehler: im Protokoll stehen keine Einträge.": "错误：记录中没有条目。",
        "Fehler: Datei ließ sich nicht umwandeln.": "错误：文件无法转换。",
        "Fehler: kein vollständiger Patch als Vorlage gefunden.": "错误：没有找到可用作模板的完整音色。",
        "Fehler: nichts angegeben (--stopp, --rhythmus, --wiedergabe, --name oder --fade).": "错误：没有给出任何参数（--stopp、--rhythmus、--wiedergabe、--name 或 --fade）。",
        "Fehler: '%s' ist keine lesbare Audiodatei.": "错误：'%s' 不是可读的音频文件。",
        "Fehler: --teile N oder --laenge SEKUNDEN angeben.": "错误：请给出 --teile N 或 --laenge 秒数。",
        "Fehler: %.1f Minuten pro Teil sind zu lang (Grenze %d Minuten).": "错误：每段 %.1f 分钟太长（上限 %d 分钟）。",
        "Fehler: '%s' gibt es nicht.": "错误：'%s' 不存在。",
        "Fehler: '%s' ist keine lesbare Audiodatei.%s": "错误：'%s' 不是可读的音频文件。%s",
        "Fehler: '%s' liegt schon vor.": "错误：'%s' 已存在。",
        "Fehler: Die Datei ließ sich nicht kürzen.": "错误：文件无法截短。",
        "Fehler: Die Umwandlung hat keine hörbare Aufnahme\n  ergeben (nur Stille). Datei prüfen: %s": "错误：转换后没有可听的内容\n  （只有静音）。请检查文件：%s",
        "Fehler: Patch%d gibt es nicht.": "错误：音色 %d 不存在。",
        "Fehler: Die Aufnahme ließ sich nicht ausblenden (kein 16-Bit-WAV?).": "错误：录音无法淡出（不是 16 位 WAV？）。",
        "Fehler: '%s' ist nicht eingelegt.": "错误：'%s' 未插入。",
        "Fehler beim Auswerfen: %s": "弹出时出错：%s",
        "Fehler: Patch%d hat keine WAV.": "错误：音色 %d 没有 WAV。",
        "Fehler: Sicherungsordner '%s' gibt es nicht.": "错误：备份文件夹 '%s' 不存在。",
        "Fehler: in '%s' liegen keine Patch-Ordner.": "错误：'%s' 中没有音色文件夹。",
        "Fehler: keine Sicherung mit einer Aufnahme für Patch%d in '%s'.\n  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade.": "错误：'%s' 中没有音色 %d 的录音备份。\n  淡出备份名为 Sicherung_<日期>_vor_fade。",
        "Fehler: Platz %d ist belegt (%s).\n  Zum Tauschen:  tauschen --patch %d --mit %d": "错误：位置 %d 已占用（%s）。\n  要交换：  tauschen --patch %d --mit %d",
        "Fehler: Nummern müssen zwischen 1 und %d liegen.": "错误：编号必须在 1 到 %d 之间。",
        "Fehler: '%s' ist ein Ordner, keine Datei.": "错误：'%s' 是文件夹，不是文件。",
        "Fehler: '%s' ist kein Pegelprotokoll (keine lesbare Textdatei).": "错误：'%s' 不是电平记录（不是可读的文本文件）。",
        "Fehler: '%s' darf nicht gelesen werden.": "错误：'%s' 不允许读取。",
        "Fehler: '%s' lässt sich nicht lesen: %s": "错误：'%s' 无法读取：%s",
        "Fehler beim Umwandeln. Die Datei hat %d Hz / %d Kanal/Kanäle;\n  nötig sind 44100 Hz und 2 Kanäle. Bitte vorher umwandeln oder\n  ffmpeg installieren.": "转换时出错。文件是 %d Hz / %d 声道；\n  需要 44100 Hz 和 2 声道。请先转换，或\n  安装 ffmpeg。",
        "Fehler: '%s' lässt sich nicht schreiben.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "错误：'%s' 无法写入。\n  请换一个文件夹：  --sicherung <路径>",
        "Fehler: Patch%d hat keine phrase.wav.": "错误：音色 %d 没有 phrase.wav。",
        "Fehler: Der Pegel ließ sich nicht ändern.": "错误：电平无法更改。",
        "Fehler: in '%s' darf nicht geschrieben werden.\n  Nimm einen anderen Ordner:  --sicherung <pfad>": "错误：'%s' 不允许写入。\n  请换一个文件夹：  --sicherung <路径>",
        "Fehler: Teil %d ließ sich nicht schneiden.": "错误：第 %d 段无法剪切。",
        "Fehler: Die Datei ist %.1f Minuten lang.\n  Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n  (Handbuch, Seite 60: \"Max. Individual Loop Time: 10 minutes\").\n  Längere Loops erscheinen auf der Karte, werden vom Pedal aber\n  nicht erkannt - und können dabei verschwinden.\n\n  So geht es weiter:\n    --kuerzen            auf 9:59 kürzen (harter Schnitt am Ende,\n                         12 ms ausgeblendet, damit es nicht knackt)\n    --laenge 480         auf 8:00 kürzen\n    --trotzdem           unverändert schreiben (zum Ausprobieren)": "错误：文件长 %.1f 分钟。\n  JamMan Solo XT 每个循环最多接受 %d 分钟\n  （手册第 60 页：\"Max. Individual Loop Time: 10 minutes\"）。\n  更长的循环会出现在卡上，但踏板不识别 -\n  并可能在过程中消失。\n\n  可以这样做：\n    --kuerzen            截到 9:59（结尾硬切，\n                         12 毫秒淡出以免爆音）\n    --laenge 480         截到 8:00\n    --trotzdem           原样写入（供试用）",
        "Fehler: --wav fehlt.": "错误：缺少 --wav。",
        "Fehler: --patch fehlt.": "错误：缺少 --patch。",
        "Fehler: --protokoll fehlt.": "错误：缺少 --protokoll。",
        "Fehler: --patch fehlt (oder --alle angeben).": "错误：缺少 --patch（或给出 --alle）。",
        "Fehler: --patch und --auf fehlen.": "错误：缺少 --patch 和 --auf。",
        "Fehler: --patch und --mit fehlen.": "错误：缺少 --patch 和 --mit。",
        "Fehler: --patch und --nach fehlen.": "错误：缺少 --patch 和 --nach。",
        "  Hinweis: Pegelprotokoll konnte nicht geschrieben werden.": "    提示：电平记录未能写入。",
        "  Protokoll: %s": "    记录：%s",
        "  %d Einträge": "    %d 条",
        "    %-10s %-24s Faktor %.4f → 1/%.4f": "      %-10s %-24s 系数 %.4f → 1/%.4f",
        "  Trockenlauf — nichts geändert. Mit --schreiben ausführen.": "    试运行 — 未做修改。用 --schreiben 执行写入。",
        "    %-10s %+6.1f → %+6.1f dB": "      %-10s %+6.1f → %+6.1f dB",
        "  Zurückgerechnet: %d Patches. Sicherung davor: %s": "    已还原：%d 个音色。之前的备份：%s",
        "  Pegel:        %.1f dB → %.1f dB Spitze": "    电平：        %.1f dB → %.1f dB 峰值",
        "                Die Anhebung wurde begrenzt, damit nichts": "                提升已受限，以免任何",
        "                über 0 dBFS geht (sonst würde übersteuert).": "                超过 0 dBFS（否则会削波）。",
        "                Achtung: %d Werte liegen an der Grenze.": "                注意：%d 个值达到上限。",
        "  Hinweis: unbekanntes WAV-Format 0x%04X - nicht angefasst.": "    提示：未知的 WAV 格式 0x%04X — 未处理。",
        "Fehler: '%s' ist kein Ordner. Ist die Karte eingelegt?": "  错误：'%s' 不是文件夹。卡片插好了吗？",
        "Fehler: auf '%s' liegt kein Ordner '%s' oder '%s'. Das ist keine JamMan-Karte.": "  错误：'%s' 中没有文件夹 '%s' 或 '%s'。这不是 JamMan 存储卡。",
        "  %d Apple-Dateien entfernt (._* und .DS_Store).": "    已删除 %d 个 Apple 文件（._* 和 .DS_Store）。",
        "  Keine Patches auf der Karte.": "    卡上没有音色。",
        "  %-10s %-34s %8s  %s": "    %-10s %-34s %8s  %s",
        "  %-10s %-34s %8s  %s%s%s": "    %-10s %-34s %8s  %s%s%s",
        "\n  %d Patches, %s belegt.": "\n  %d 个音色，%s 个已占用。",
        "  Sicherung beim Start: Karte unverändert, nichts zu tun.": "    启动备份：卡片未变，无需处理。",
        "  Sicherung beim Start: %s": "    启动备份：%s",
        "    gesichert: %d Patches, %.0f MB": "      已备份：%d 个音色，%.0f MB",
        "    alte Sicherung entfernt: %s": "      已删除旧备份：%s",
        "  Quelle: %s": "    来源：%s",
        "  Ziel:   %s": "    目标：%s",
        "  Trockenlauf — nichts kopiert. Mit --schreiben ausführen.": "    试运行 — 未复制。用 --schreiben 执行写入。",
        "  Gesichert: %d Patches, %.0f MB.": "    已备份：%d 个音色，%.0f MB。",
        "  Datei:  %s": "    文件：%s",
        "  Dauer:  %s": "    时长：%s",
        "  Teile:  %d × etwa %s": "    分段：%d × 约 %s",
        "  Suche musikalisch brauchbare Schnittstellen …": "    正在寻找可用的剪切点 …",
        "  Hinweis: keine brauchbaren Pausen gefunden (%s) -": "    提示：未找到可用的停顿（%s）—",
        "           es wird gleichmäßig geschnitten.": "             改为均匀切分。",
        "  Pause an der Naht wird herausgenommen": "    接缝处的停顿已去除",
        "    Teil %d: %7.1f – %7.1f s  (%s)": "      第 %d 段：%7.1f – %7.1f s（%s）",
        "  Hinweis: Teil %d wäre %.1f min - er wird auf 9:59 gekürzt.": "    提示：第 %d 段会是 %.1f 分钟 — 将截到 9:59。",
        "    Teil %d: %s – %s": "      第 %d 段：%s – %s",
        "  Trockenlauf — nichts angelegt. Mit --schreiben ausführen.": "    试运行 — 未创建。用 --schreiben 执行写入。",
        "  Quelle:       %s": "    来源：        %s",
        "  Dauer:        %s": "    时长：        %s",
        "  Format:       %d Hz / %d Bit / %d Kanal/Kanäle%s": "    格式：        %d Hz / %d 位 / %d 声道%s",
        "  Stopp:        %s": "    停止：        %s",
        "  Rhythmus:     %s": "    节奏：        %s",
        "  Wiedergabe:   %s": "    播放：        %s",
        "  Fade:         letzte %.1f s werden ausgeblendet": "    淡出：        最后 %.1f 秒淡出",
        "  Pegel:        auf -1 dB Spitze normieren": "    电平：        标准化到 -1 dB 峰值",
        "  Pegel:        %+.1f dB": "    电平：        %+.1f dB",
        "  Neuer Patch:  Patch%s  (Name: %s)": "    新音色：      Patch%s（名称：%s）",
        "  Vorlage:      %s": "    模板：        %s",
        "  Trockenlauf — nichts geschrieben. Mit --schreiben ausführen.": "    试运行 — 未写入。用 --schreiben 执行写入。",
        "  Kürzen:       auf %s geschnitten (harter Schnitt, 12 ms ausgeblendet)": "    截短：        截到 %s（硬切，12 毫秒淡出）",
        "  Kopf:         WAV-Kopf auf einfaches PCM umgeschrieben": "    文件头：      WAV 头已改写为普通 PCM",
        "  Fertig: %s angelegt.": "    完成：已创建 %s。",
        "  Karte auswerfen, in den JamMan stecken — der Patch erscheint als Patch%s.": "    弹出卡片，插入 JamMan — 音色显示为 Patch%s。",
        "  Patch:        %s  (%s)": "    音色：        %s（%s）",
        "  Stopp:        %s (%s)": "    停止：        %s（%s）",
        "  Rhythmus:     %s (%s)": "    节奏：        %s（%s）",
        "  Wiedergabe:   %s (IsLoop %s)": "    播放：        %s（IsLoop %s）",
        "  Name:         %s": "    名称：        %s",
        "  Fade:         letzte %.1f s der Aufnahme ausblenden": "    淡出：        录音最后 %.1f 秒淡出",
        "                (verändert die Audiodatei; vorher wird gesichert)": "                （会改变音频文件；之前会自动备份）",
        "  Geändert. Sicherung: %s": "    已修改。备份：%s",
        "  %s Sie kann jetzt abgezogen werden.": "    %s 现在可以取出了。",
        "  Schreibweise des Geräts: Patch%0*d": "    设备的写法：Patch%0*d",
        "  Alle Ordner heißen schon so. Nichts zu tun.": "    所有文件夹已是此写法。无需处理。",
        "  Zu ändern: %d": "    待修改：%d",
        "    %-12s ->  %s": "      %-12s ->  %s",
        "    %-12s FEHLER: %s": "      %-12s 错误：%s",
        "  Fertig: %d Ordner umbenannt, %d Fehler, %d Patches lesbar.": "    完成：重命名 %d 个文件夹，%d 个错误，%d 个音色可读。",
        "  Zuordnung liegt in: %s": "    对应关系在：%s",
        "  %d Apple-Dateien auf der Karte. Mit --schreiben entfernen.": "    卡上有 %d 个 Apple 文件。用 --schreiben 删除。",
        "  Fertig.": "    完成。",
        "  Spielt \"%s\" (%s) … (Strg+C hält an)": "    正在播放“%s”（%s）…（Ctrl+C 停止）",
        "  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB": "    目标：%.1f dB 响度（RMS），峰值最高 %.1f dB",
        "  Patches: %d": "    音色：%d",
        "  %-10s %-24s %8s": "    %-10s %-24s %8s",
        "  %-10s %-24s %+7.1f dB": "    %-10s %-24s %+7.1f dB",
        "  Sicherung: %s": "    备份：%s",
        "  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s": "    %-10s %-24s %+6.1f → %+6.1f dB（峰值 %+.1f dB）%s",
        "  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.": "    完成：%d 个音色已标准化，其中 %d 个在峰值处受限。",
        "  Patches in der Sicherung: %d": "    备份中的音色：%d",
        "  Patches auf der Karte:    %d": "    卡上的音色：    %d",
        "  Hinweis: diese gibt es auf der Karte nicht und werden neu angelegt:": "    提示：这些在卡上不存在，将被新建：",
        "           %s": "             %s",
        "  Trockenlauf — nichts zurückgeholt. Mit --schreiben ausführen.": "    试运行 — 未恢复。用 --schreiben 执行写入。",
        "  Zurückgeholt. Der Zustand davor liegt in: %s": "    已恢复。之前的状态在：%s",
        "  Patch:      %s  (%s)": "    音色：      %s（%s）",
        "  Sicherung:  %s": "    备份：      %s",
        "    %-22s %8.2f MB": "      %-22s %8.2f MB",
        "  %s  (%s)": "    %s（%s）",
        "  →  %s": "    →  %s",
        "  Trockenlauf — nichts umbenannt. Mit --schreiben ausführen.": "    试运行 — 未重命名。用 --schreiben 执行写入。",
        "  Fertig. Sicherung: %s": "    完成。备份：%s",
        "  Nichts zu tun.": "    无需处理。",
        "  Achtung: %s und %s haben unterschiedliche Stellenzahl.": "    注意：%s 与 %s 的位数不同。",
        "           Der JamMan sortiert nach Namen - der Patch landet dann": "             JamMan 按名称排序 — 音色将落在",
        "           auf dem Platz, den sein neuer Name angibt.": "             其新名称所指的位置。",
        "  Tauschen:": "    交换：",
        "    Platz %-4s %-24s  →  Platz %s": "      位置 %-4s %-24s  →  位置 %s",
        "  Trockenlauf — nichts getauscht. Mit --schreiben ausführen.": "    试运行 — 未交换。用 --schreiben 执行写入。",
        "  Getauscht. Sicherung: %s": "    已交换。备份：%s",
        "  Wird gelöscht: %s  (%s)": "    将被删除：%s（%s）",
        "  Vorher wird er gesichert nach: %s": "    先备份到：%s",
        "  Trockenlauf — nichts gelöscht. Mit --schreiben ausführen.": "    试运行 — 未删除。用 --schreiben 执行写入。",
        "  Gelöscht. Sicherung: %s": "    已删除。备份：%s",
        "  Karte auswerfen, in den JamMan stecken — der Patch ist weg.": "    弹出卡片，插入 JamMan — 音色已删除。",
        "  Neue Reihenfolge:": "    新顺序：",
        "    Platz %d:  %-10s  %s%s": "      位置 %d：  %-10s  %s%s",
        "  Verschoben. Sicherung: %s": "    已移动。备份：%s",
        "  JamMan Solo XT — Speicherkarte verwalten": "    JamMan Solo XT — 管理存储卡",
        "  Keine Karte gefunden unter %s.": "    在 %s 下未找到存储卡。",
        "  Karte: %s": "    存储卡：%s",
        "  1  Patches auflisten": "    1  列出音色",
        "  2  Patch anhören": "    2  试听音色",
        "  3  Datei aufspielen": "    3  导入文件",
        "  4  Patch ändern (Stopp, Wiedergabe, Name)": "    4  修改音色（停止、播放、名称）",
        "  5  Patch verschieben": "    5  移动音色",
        "  6  Patch löschen": "    6  删除音色",
        "  7  Alles sichern": "    7  全部备份",
        "  8  Karte auswerfen": "    8  弹出卡片",
        "  0  Beenden": "    0  退出",
        "  %s": "    %s",
        "  System:        %s %s": "    系统：        %s %s",
        "  Python:        %s": "    Python：      %s",
        "  Fassung:       %s": "    版本：        %s",
        "  Tk:            %s": "    Tk：          %s",
        "  Tk-Modul:      %s": "    Tk 模块：     %s",
        "  Tk:            NICHT verfügbar — %s": "    Tk：          不可用 — %s",
        "                 (Fenster geht mit diesem Python nicht;": "                    （此 Python 无法显示窗口；",
        "                  das Textmenü geht trotzdem: jamman.py menu)": "                     文本菜单仍可用：jamman.py menu）",
        "  Ton:           winsound (Windows-eigen)": "    声音：        winsound（Windows 自带）",
        "  Ton:           afplay": "    声音：        afplay",
        "  Ton:           %s": "    声音：        %s",
        "  Kartenwurzeln: %s": "    卡片搜索位置：%s",
        "  Einstellungen: %s": "    设置：        %s",
        "  ffmpeg:        %s": "    ffmpeg：      %s",
        "  Schrift:       %s": "    字体：        %s",
        "  Pythons auf diesem Rechner:": "    本机上的 Python：",
        "    %-62s %s": "      %-62s %s",
        "  Geprüfte Orte:": "    已检查的位置：",
        "  Laufwerke mit JamManSoloXT:": "    含 JamManSoloXT 的驱动器：",
        "    %-24s %3d Patches  %s": "      %-24s %3d 个音色  %s",
        "  gewählt:       %s": "    已选择：      %s",
        "  Patches:       %d": "    音色：        %d",
        "  ACHTUNG: das Dateisystem liefert Lesefehler.": "    警告：文件系统报告读取错误。",
        "  Betroffene Einträge (erste drei): %s": "    受影响的条目（前三个）：%s",
        "  Inhalt von %s:": "    %s 的内容：",
        "    %-24r %s, %s": "      %-24r %s, %s",
        "    nicht lesbar: %s": "      无法读取：%s",
        "    %-10s %s": "      %-10s %s",
        "  erste Zeile im Fenster:": "    窗口中的第一行：",
        "    %r": "      %r",
        "JamMan Solo XT": "  JamMan Solo XT",
        "WAV-Format": "WAV 格式",
        "%d Hz / %d Bit / %d K": "%d Hz / %d 位 / %d 声道",
        "  %-28s  (gibt es nicht)": "  %-28s  （不存在）",
        "  %-28s  (nicht lesbar: %s)": "  %-28s  （无法读取：%s）",
        "  %-28s  (leer)": "  %-28s  （空）",
        "  %-28s  %s mit %s Patches": "  %-28s  %s，共 %s 个音色",
        "kein nutzbares Tk": "没有可用的 Tk",
        "Sprache der Ausgabe (Vorgabe: aus dem System)": "输出语言（默认：跟随系统）",
        "Laufwerk der Karte (Vorgabe: automatisch erkannt)": "存储卡驱动器（默认：自动识别）",
        "Zielordner für 'sichern'": "「备份」的目标文件夹",
        "Audiodatei für 'aufspielen'": "「导入」的音频文件",
        "Name des Patches (Vorgabe: Dateiname)": "音色名称（默认：文件名）",
        "Patch-Nummer": "音色编号",
        "Zielplatz für 'verschieben'": "「移动」的目标位置",
        "Tauschpartner für 'tauschen'": "「交换」的对方槽位",
        "Zielplatz für 'platzieren'": "「放置」的目标位置",
        "Sicherungsordner für 'zuruecknehmen'": "「恢复」的备份文件夹",
        "Pegelprotokoll für 'pegel-umkehren'": "「还原电平」的电平记录",
        "bei 'zuruecknehmen': alle Patches aus der Sicherung": "「恢复」时：备份中的全部音色",
        "Ziellautheit für 'alle-normieren' (Vorgabe -18 dB)": "「全部标准化」的目标响度（默认 -18 dB）",
        "Spitzengrenze für 'alle-normieren' (Vorgabe -1 dB)": "「全部标准化」的峰值上限（默认 -1 dB）",
        "zu lange Datei auf 9:59 kürzen": "把过长的文件截到 9:59",
        "beim Teilen die Pause an der Naht stehen lassen": "分割时保留接缝处的停顿",
        "beim Teilen gleichmäßig schneiden statt an Pausen": "分割时均匀切分，而不是在停顿处",
        "Datei in N gleich lange Patches aufteilen": "把文件分成 N 个等长音色",
        "zu lange Datei auf diese Länge kürzen": "把过长的文件截到这个长度",
        "auch Loops über 10 Minuten schreiben (Pedal nimmt sie nicht an)": "也写入超过 10 分钟的循环（踏板不接受）",
        "Fade am Ende der Datei einrechnen (z. B. 3)": "计入文件末尾的淡出（例如 3）",
        "Lautstärke um DB ändern (z. B. -3 oder +2)": "按 DB 改变音量（例如 -3 或 +2）",
        "Anhebung NICHT auf 0 dBFS begrenzen (kann übersteuern)": "提升时不要限制在 0 dBFS（可能削波）",
        "auf -1 dB Spitzenpegel normieren": "标准化到 -1 dB 峰值",
        "Stopp-Verhalten des neuen Patches (ohne Angabe: wie Vorlage)": "新音色的停止方式（不填：同模板）",
        "Loop oder Single (IsLoop in phrase.xml; ohne Angabe: wie Vorlage)": "循环或单次（phrase.xml 中的 IsLoop；不填：同模板）",
        "Rhythmus des neuen Patches (ohne Angabe: wie Vorlage)": "新音色的节奏（不填：同模板）",
        "beim Verschieben nur die Namen sichern (schnell, kein Kopieren)": "移动时只备份名称（快，不复制）",
        "Ordner für die Sicherung vor Löschen/Verschieben": "删除/移动前备份的文件夹",
        "wirklich verändern (sonst Trockenlauf)": "真正修改（否则只是试运行）",
        "was zu tun ist (Liste unten; englische Namen gehen auch)": "要做什么（见下方列表；英文名称也可以）",
        "Einstellungen: %s": "设置：%s",
        "Keine Karte gefunden": "未找到存储卡",
        "Neue Fassung verfügbar": "有新的版本",
        "Es gibt eine neuere Fassung: %s. Sie benutzen %s.": "有更新的版本：%s。您使用的是 %s。",
        "Zur Download-Seite wechseln?": "要打开下载页面吗？",
        "Es liegt keine JamMan-Karte im Rechner.": "电脑里没有 JamMan 存储卡。",
        "Nimm die microSD aus dem Pedal und stecke sie in einen Kartenleser oder Kartenslot. Über das USB-Kabel am Pedal erscheint sie nicht als Laufwerk.": "请把 microSD 从踏板中取出，插入读卡器或卡槽。通过踏板的 USB 线连接时，它不会显示为驱动器。",
        "Ordner selbst wählen?": "自己选择文件夹吗？",
        "Ordner der Karte wählen": "选择存储卡文件夹",
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
    """Sprache des Systems erraten.

    Reihenfolge:
      1. JAMMAN_SPRACHE (zum Ausprobieren gesetzt)
      2. macOS: die eingestellte Systemsprache (`AppleLanguages`)
      3. LANG / LC_ALL / LC_MESSAGES

    Anlaß (18.11.2026): Auf einem Intel-Mac startete das Werkzeug englisch,
    obwohl der Nutzer deutsch erwartet hatte — das Terminal meldete `en`,
    die Systemsprache war nicht gefragt worden. `C`/`POSIX` werden
    übersprungen, weil sie keine Sprache nennen.
    """
    eigene = os.environ.get("JAMMAN_SPRACHE")
    if eigene:
        return eigene[:2]

    if sys.platform == "darwin":
        try:
            roh = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True, timeout=5).stdout
            treffer = re.findall(r'"([a-zA-Z]{2})', roh)
            if treffer:
                return treffer[0].lower()
        except Exception:
            pass

    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        wert = os.environ.get(name)
        if wert and wert[:2].lower() not in ("c.", "c ", "po"):
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


GUI_FENSTER = None
# Anlaß (19.11.2026): Aus einem Arbeitsfaden heraus entstanden weitere
# Balkenfenster, die nie wieder zugingen. Tk gehört in den Hauptfaden —
# deshalb merkt sich das Werkzeug, ob gerade ein Faden arbeitet.
_HINTERGRUND = threading.local()


def _im_hintergrund():
    return getattr(_HINTERGRUND, "aktiv", False)


def _groesse_von(pfade):
    gesamt = 0
    for pfad in pfade:
        for w, _d, ds in os.walk(pfad):
            for x in ds:
                try:
                    gesamt += os.path.getsize(os.path.join(w, x))
                except OSError:
                    pass
    return gesamt


def sicher_ablegen(basis, patch_pfade, grund, wurzel=None, melden=None):
    """Kopiert Patches vorher weg. Gibt den Zielordner zurück."""
    ordner = os.path.expanduser(wurzel or SICHERUNG_WURZEL)
    ziel = freier_name(os.path.join(
        ordner, "Sicherung_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        + "_" + grund))
    try:
        os.makedirs(ziel, exist_ok=True)
    except PermissionError:
        raise SystemExit(T("Fehler: in '%s' darf nicht geschrieben werden.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>") % ordner)
    # Ohne ausdrückliche Meldung: bei mehr als 30 MB einen eigenen Balken
    # zeigen (19.11.2026, Wunsch: Fortschritt bei allen Datei-Vorgängen).
    eigenes = None
    if (melden is None and GUI_FENSTER is not None and Fortschritt.offen == 0
            and not _im_hintergrund()
            and _groesse_von(patch_pfade) > 30 * 1048576):
        eigenes = Fortschritt(GUI_FENSTER, T("Sicherung anlegen"))
        melden = eigenes.setzen
    try:
        return _ablegen_kopieren(ziel, patch_pfade, melden)
    finally:
        if eigenes is not None:
            eigenes.schliessen()


def _ablegen_kopieren(ziel, patch_pfade, melden):
    for i, pfad in enumerate(patch_pfade, 1):
        def _teil(anteil, text, _i=i, _p=pfad):
            if melden:
                # _i statt i: i ist die Schleifenvariable und kann sich
                # verschieben, wenn der Rückruf später kommt (ruff: B023).
                gesamt = (_i - 1 + anteil) / float(len(patch_pfade) or 1)
                melden(gesamt, "%s — %s" % (os.path.basename(_p), text))
        kopiere_baum(pfad, os.path.join(ziel, os.path.basename(pfad)), _teil)
    return ziel


def zuruecknehmen_kandidaten(ordner, nummer):
    """Sicherungen, die für diesen Platz eine Aufnahme enthalten, jüngste zuerst.

    Zwei Bauformen kommen vor:
      Sicherung_2026-11-15_17-53-02_vor_normieren/Patch02/…      (einzelne Patches)
      JamManSicherung_2026-11-19_03-29/JamManSoloXT/Patch02/…    (ganze Karte)

    Anlaß (19.11.2026): Nur die erste Form wurde gesucht. Nach dem Umkehren
    bot „Zurücknehmen" deshalb eine vier Tage alte Sicherung an — die von heute
    lag als Vollsicherung daneben und war unsichtbar. Sortiert wird jetzt nach
    der Änderungszeit des Patch-Ordners, nicht nach dem Namen: „Sicherung_…"
    und „JamManSicherung_…" sortieren sonst nicht chronologisch.
    """
    # Wird der Ordner EINER Sicherung übergeben (…/Sicherung_…_vor_fade),
    # liegt der Patch direkt darin. Anlaß (19.11.2026): „Zurücknehmen" aus
    # einer bestimmten Sicherung scheiterte, weil hier Unterordner erwartet
    # wurden.
    for form in ("Patch%02d" % nummer, "Patch%d" % nummer):
        for k in (os.path.join(ordner, form),
                  os.path.join(ordner, UNTERORDNER, form)):
            if os.path.isdir(k) and (os.path.exists(os.path.join(k, "PhraseA", "phrase.wav"))
                                     or os.path.exists(os.path.join(k, "patch.xml"))):
                return [k]

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
        # Sammelsicherungen sind Archive, keine Undo-Punkte: in ihnen sind die
        # Plätze neu durchnumeriert, ein „Patch02" dort ist nicht der Platz 2
        # der Karte (19.11.2026).
        if name.endswith("_rettung"):
            continue
        for form in ("Patch%02d" % nummer, "Patch%d" % nummer):
            for k in (os.path.join(ordner, name, form),
                      os.path.join(ordner, name, UNTERORDNER, form)):
                if not os.path.isdir(k):
                    continue
                # Auch Sicherungen, die nur patch.xml und phrase.xml enthalten
                # (Name, Stoppart, Wiedergabe/Rückwärts) sind Undo-Punkte —
                # bisher fielen sie heraus, weil die Aufnahme fehlte. Anlaß
                # (19.11.2026): Nach dem Umkehren bot „Zurücknehmen" deshalb
                # eine alte Sicherung an.
                if (os.path.exists(os.path.join(k, "PhraseA", "phrase.wav"))
                        or os.path.exists(os.path.join(k, "patch.xml"))):
                    try:
                        wann = os.path.getmtime(k)
                    except OSError:
                        wann = 0
                    treffer.append((wann, k))
                    break
            else:
                continue
            break
    treffer.sort(key=lambda x: x[0], reverse=True)
    return [k for _, k in treffer]


def befehl_alle_normieren(basis, schreiben, ziel_rms_db=-18.0, grenze_db=-1.0,
                          wurzel=None, fortschritt=None):
    """Alle Patches auf gleiche Lautheit bringen - für die Bühne.

    Normiert wird auf den Effektivwert (RMS), nicht auf die Spitze:
    gleich laute Spitzen bedeuten nicht gleich laute Loop. Die Spitze
    wird zusätzlich bei grenze_db gedeckelt, damit nichts übersteuert.
    """
    alle = patches(basis)
    if not alle:
        raise SystemExit(T("Keine Patches auf der Karte."))
    print(T("  Ziel: %.1f dB Lautheit (RMS), Spitze höchstens %.1f dB")
          % (ziel_rms_db, grenze_db))
    print(T("  Patches: %d") % len(alle))
    if not schreiben:
        print()
        print(T("  %-10s %-24s %8s") % ("Ordner", "Name", "Lautheit jetzt"))
        for _nummer, name, pfad in alle:
            w = phrase_wav(pfad)
            if not w:
                continue
            _, rms = pegel_info(w)
            print(T("  %-10s %-24s %+7.1f dB") % (name, patch_name(pfad)[:24], rms))
        print()
        print(T("  Trockenlauf — nichts geändert. Mit --schreiben ausführen."))
        return

    # Erst messen, dann sichern: Patches, die schon auf Ziel liegen, werden
    # weder gesichert noch neu geschrieben (19.11.2026). Vorher wurde jeder
    # WAV neu geschrieben und die ganze Karte gesichert, auch wenn sich nichts
    # änderte — bei 35 Patches sind das 1,3 GB Sicherung für nichts.
    zu_tun = []
    schon_gut = 0
    for nummer, name, pfad in alle:
        w = phrase_wav(pfad)
        if not w:
            continue
        spitze, rms = pegel_info(w)
        if rms is None or rms <= -998:
            continue
        faktor = 10 ** ((ziel_rms_db - rms) / 20.0)
        grenze = 32767.0 * (10 ** (grenze_db / 20.0))
        if 32768.0 * (10 ** (spitze / 20.0)) * faktor > grenze:
            faktor = grenze / (32768.0 * (10 ** (spitze / 20.0)))
        if abs(20 * math.log10(faktor)) < 0.05:
            schon_gut += 1
            continue
        zu_tun.append((nummer, name, pfad))
    if schon_gut:
        print(T("  Schon auf Ziel: %d von %d Patches — unverändert gelassen.")
              % (schon_gut, len(alle)))
    if not zu_tun:
        print(T("  Nichts zu tun: alle Patches liegen bereits auf der Ziel-Lautheit."))
        return 0, schon_gut
    alle = zu_tun

    ziel = sicher_ablegen(basis, [pf for _, _, pf in alle], "vor_normieren", wurzel)
    print(T("  Sicherung: %s") % ziel)
    print()
    geaendert = 0
    gedeckelt = 0
    zaehler = 0
    protokoll_eintraege = []
    for _nummer, name, pfad in alle:
        zaehler += 1
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
            try:
                fortschritt(zaehler / float(len(alle) or 1),
                            "normiert: %s (%d/%d)" % (patch_name(pfad)[:24],
                                                     zaehler, len(alle)))
            except TypeError:
                fortschritt("normiert: %s (%d/%d)" % (patch_name(pfad)[:24],
                                                     geaendert, len(alle)))
        print(T("  %-10s %-24s %+6.1f → %+6.1f dB  (Spitze %+.1f dB)%s")
              % (name, patch_name(pfad)[:24], vorher[1], nachher[1],
                 nachher[0], "  [gedeckelt]" if ergebnis["begrenzt"] else ""))
    saeubere_karte(basis)
    schreibe_pegelprotokoll(basis, protokoll_eintraege, "alle_normieren", wurzel)
    print()
    zusatz = ""
    if schon_gut == 1:
        zusatz = T("  Ein Patch war schon auf Ziel und blieb unverändert.")
    elif schon_gut:
        zusatz = T("  %d Patches waren schon auf Ziel und blieben unverändert.") % schon_gut
    print(T("  Fertig: %d Patches normiert, %d davon in der Spitze gedeckelt.")
          % (geaendert, gedeckelt))
    if zusatz:
        print(zusatz)
    return geaendert, schon_gut


def befehl_zuruecknehmen_alle(basis, aus, schreiben, wurzel=None):
    """Alle Patches aus einer Sicherung zurückholen.

    Für den Fall, dass eine Sammelaktion (z. B. alle normieren) rückgängig
    gemacht werden soll.
    """
    quelle = os.path.expanduser(aus or "")
    if not os.path.isdir(quelle):
        raise SystemExit(T("Fehler: Sicherungsordner '%s' gibt es nicht.") % quelle)
    vorhanden = sorted(d for d in os.listdir(quelle)
                       if re.fullmatch(r"Patch\d+", d))
    if not vorhanden:
        raise SystemExit(T("Fehler: in '%s' liegen keine Patch-Ordner.") % quelle)
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


def ist_einstellungssicherung(pfad):
    """Enthält diese Sicherung nur Einstellungen (keine Aufnahme)?"""
    return (os.path.isdir(pfad)
            and not os.path.exists(os.path.join(pfad, "PhraseA", "phrase.wav"))
            and os.path.exists(os.path.join(pfad, "patch.xml")))


def befehl_zuruecknehmen_einstellungen(basis, nummer, schreiben, quelle, wurzel=None):
    """Name, Stoppart, Rhythmus und Wiedergabe aus einer Sicherung zurückholen.

    Die Aufnahme bleibt dabei unberührt — sie war in dieser Sicherung nicht
    enthalten. Damit läßt sich zum Beispiel ein Rückwärts-Lauf zurücknehmen
    (19.11.2026).
    """
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % nummer)
    ziel = alle[nummer]
    print(T("  Sicherung: %s") % quelle)
    if not schreiben:
        print(T("  Trockenlauf — nichts geändert."))
        return quelle
    sicher_ablegen_settings = sicherung_der_einstellungen(
        basis, ziel, "vor_zuruecknehmen", wurzel)
    print(T("  Jetziger Stand gesichert: %s") % sicher_ablegen_settings)
    import shutil as _shutil
    for name in ("patch.xml", os.path.join("PhraseA", "phrase.xml")):
        quelle_datei = os.path.join(quelle, name)
        if os.path.exists(quelle_datei):
            ziel_datei = os.path.join(ziel, name)
            os.makedirs(os.path.dirname(ziel_datei), exist_ok=True)
            _shutil.copy2(quelle_datei, ziel_datei)
    print(T("  Einstellungen von Patch%d zurückgeholt.")
          % nummer)
    return quelle


def befehl_zuruecknehmen(basis, nummer, schreiben, wurzel=None, aus=None):
    """Holt eine frühere Fassung eines Patches aus der Sicherung zurück.

    Gedacht für den Fade: der verändert die Aufnahme, und rückgängig geht
    das nur über die Sicherung, die dabei angelegt wurde.
    """
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if nummer not in alle:
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % nummer)
    ordner = os.path.expanduser(aus or wurzel or SICHERUNG_WURZEL)
    if not os.path.isdir(ordner):
        raise SystemExit(T("Fehler: Sicherungsordner '%s' gibt es nicht.") % ordner)

    kandidaten = zuruecknehmen_kandidaten(ordner, nummer)
    if not kandidaten:
        raise SystemExit(T("Fehler: keine Sicherung mit einer Aufnahme für "
                         "Patch%d in '%s'.\n"
                         "  Fade-Sicherungen heißen Sicherung_<Datum>_vor_fade.")
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


def luecken_in(basis):
    """Fehlende Platznummern zwischen 1 und dem letzten belegten Platz.

    Anlaß (19.11.2026): Nach dem Löschen eines Platzes klafft eine Lücke, und
    die Suche nach „10" findet nichts, weil der Ordnername Patch011 heißt.
    Die Liste selbst wird NICHT umbenannt — das geschieht nur auf Knopfdruck
    (plaetze_aufruecken_lokal), weil sich damit alle späteren Platznummern
    ändern und gesicherte Setlists nicht mehr stimmen.
    """
    nummern = [n for n, _, _ in patches(basis)]
    if not nummern:
        return []
    return [n for n in range(1, max(nummern) + 1) if n not in nummern]


def stellenzahl(basis):
    """Wie viele Ziffern die Ordnernamen haben (Patch011 → 3, Patch11 → 2)."""
    for _, name, _ in patches(basis):
        return len(name[5:])
    return 2


def aufruecken_zuege(basis):
    """Welche Plätze wohin rücken: [(alt, neu)] in aufsteigender Reihenfolge."""
    nummern = sorted(n for n, _, _ in patches(basis))
    return [(alt, i) for i, alt in enumerate(nummern, 1) if i != alt]


def plaetze_aufruecken_lokal(basis, zuege=None, wurzel=None):
    """Belegte Plätze lückenlos nach vorn ziehen — auf der Karte im Leser.

    Die Stellenzahl der Ordnernamen bleibt erhalten (Patch011 → Patch010,
    nicht Patch10): das Pedal und JamManager XT arbeiten mit den vorhandenen
    Namen, und ein Wechsel des Schemas hat am 26.10.2026 die Karte unlesbar
    gemacht. Vorher wird die Zuordnung als Textdatei gesichert.

    Rückgabe: die ausgeführten Züge [(alt, neu)].
    """
    if zuege is None:
        zuege = aufruecken_zuege(basis)
    if not zuege:
        return []
    breite = stellenzahl(basis)
    sicherung_der_namen(basis, "vor_aufruecken", wurzel)
    for alt, neu in zuege:
        alt_pfad = os.path.join(basis, "Patch%0*d" % (breite, alt))
        neu_pfad = os.path.join(basis, "Patch%0*d" % (breite, neu))
        if os.path.exists(neu_pfad) and neu != alt:
            raise SystemExit(T("Fehler: Platz %d ist belegt (%s).") % (neu, neu_pfad))
        os.rename(alt_pfad, neu_pfad)
    try:
        saeubere_karte(basis)
    except Exception:
        pass
    return zuege


def befehl_platzieren(basis, von, auf, schreiben, wurzel=None):
    """Einen Patch auf eine bestimmte Platznummer legen.

    Beispiel: Patch200 soll Patch28 werden. Der Platz muss frei sein;
    ist er belegt, hilft "tauschen" weiter.
    """
    alle = dict((n, pf) for n, _, pf in patches(basis))
    if von not in alle:
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % von)
    if auf in alle and auf != von:
        raise SystemExit(T("Fehler: Platz %d ist belegt (%s).\n"
                         "  Zum Tauschen:  tauschen --patch %d --mit %d")
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
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % fehlend)
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
        raise SystemExit(T("Fehler: Patch%d gibt es nicht.") % nummer)
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
        raise SystemExit(T("Fehler: in '%s' darf nicht geschrieben werden.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>") % ordner)
    name = "Umbenennung_%s_%s.txt" % (datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), grund)
    ziel = freier_name(os.path.join(ordner, name))
    try:
        f = open(ziel, "w", encoding="utf-8")
    except OSError:
        raise SystemExit(T("Fehler: '%s' lässt sich nicht schreiben.\n"
                         "  Nimm einen anderen Ordner:  --sicherung <pfad>") % ziel)
    with f:
        f.write("Reihenfolge vor der Änderung (%s)\n" % datetime.now().isoformat(timespec="seconds"))
        f.write("Ordner\tName\tKennung\n")
        for _nummer, ordnername, pfad in patches(basis):
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
        raise SystemExit(T("Fehler: Nummern müssen zwischen 1 und %d liegen." )% len(reihe))
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
            neu = input(T("  Anderer Pfad (leer = beenden): ")).strip()
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
        try:
            wahl = input(T("  Auswahl: ")).strip()
        except EOFError:
            # Kein Terminal (z. B. Doppelklick, oder die Eingabe wurde
            # geschlossen): freundlich beenden statt Absturzbericht
            # (19.11.2026).
            print()
            print(T("  Keine Eingabe möglich — Ende."))
            return

        try:
            if wahl == "0":
                return
            elif wahl == "1":
                befehl_liste(basis)
            elif wahl == "2":
                befehl_abspielen(basis, int(input(T("  Patch-Nummer: ")).strip()))
            elif wahl == "3":
                datei = input(T("  Pfad zur Audiodatei: ")).strip().strip("'")
                name = input(T("  Name (leer = Dateiname): ")).strip() or None
                st = input(T("  Stopp (fade/instant, leer = wie Vorlage): ")).strip() or None
                wg = input(T("  Wiedergabe (loop/single, leer = wie Vorlage): ")).strip() or None
                if input(T("  Wirklich schreiben? (j/n): ")).strip().lower() in ("j", "y", "ja", "yes"):
                    befehl_aufspielen(basis, datei, name, True, st, None, wg)
            elif wahl == "4":
                nr = int(input(T("  Patch-Nummer: ")).strip())
                st = input(T("  Stopp (fade/instant, leer = lassen): ")).strip() or None
                wg = input(T("  Wiedergabe (loop/single, leer = lassen): ")).strip() or None
                nm = input(T("  Neuer Name (leer = lassen): ")).strip() or None
                if input(T("  Wirklich schreiben? (j/n): ")).strip().lower() in ("j", "y", "ja", "yes"):
                    befehl_aendern(basis, nr, True, st, None, wg, nm)
            elif wahl == "5":
                von = int(input(T("  Welcher Patch: ")).strip())
                nach = int(input(T("  An welche Stelle: ")).strip())
                if input(T("  Wirklich verschieben? (j/n): ")).strip().lower() in ("j", "y", "ja", "yes"):
                    befehl_verschieben(basis, von, nach, True)
            elif wahl == "6":
                nr = int(input(T("  Welcher Patch: ")).strip())
                if input(T("  Wirklich löschen? (j/n): ")).strip().lower() in ("j", "y", "ja", "yes"):
                    befehl_loeschen(basis, nr, True)
            elif wahl == "7":
                ziel = input(T("  Zielordner (leer = ~/JamMan-Sicherung): ")).strip() or "~/JamMan-Sicherung"
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


def bericht_fenster(haupfenster, text):
    """Zeigt den Bericht des Selbsttests in einem Fenster mit Kopierknopf."""
    import tkinter as tk
    f = tk.Toplevel(haupfenster)
    f.title(T("Selbsttest"))
    f.configure(bg=FARBE_HINTERGRUND)
    f.geometry("760x520+%d+%d" % (haupfenster.winfo_rootx() + 40,
                                  haupfenster.winfo_rooty() + 40))
    tk.Label(f, text=T("Bericht des Selbsttests"), bg=FARBE_HINTERGRUND,
             fg=FARBE_SCHRIFT, font=schrift(12, fest=True)).pack(pady=(12, 6))
    rahmen = tk.Frame(f, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=14)
    anzeige = tk.Text(rahmen, bg=FARBE_LISTE, fg=FARBE_SCHRIFT, relief="flat",
                      font=schrift(10), wrap="word")
    anzeige.pack(side="left", fill="both", expand=True)
    roll = tk.Scrollbar(rahmen, command=anzeige.yview)
    roll.pack(side="right", fill="y")
    anzeige.config(yscrollcommand=roll.set)
    anzeige.insert("1.0", text)
    anzeige.see("end")

    leiste = tk.Frame(f, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=14, pady=10)

    def kopieren():
        try:
            f.clipboard_clear()
            f.clipboard_append(text)
            meldung = T("In die Zwischenablage kopiert.")
        except Exception:
            meldung = T("Kopieren ging nicht.")
        status.config(text=meldung)

    def als_issue():
        """Bericht als GitHub-Issue vorbereiten — abschicken muß der Nutzer.

        Anlaß (19.11.2026): Es soll nichts von allein an den Autor gehen.
        Der Bericht enthält Systemdaten und Dateipfade; wer ihn schicken will,
        tut das selbst. Dieser Knopf füllt nur das Formular vor.
        """
        import urllib.parse
        import webbrowser
        titel = "Selbsttest: %s" % FASSUNG
        rumpf = (T("Bericht des Selbsttests") + "\n\n```\n" + text[-5000:]
                 + "\n```\n")
        ziel = ("https://github.com/Suilui1/jamman-card-tool/issues/new"
                "?title=%s&body=%s" % (urllib.parse.quote(titel),
                                       urllib.parse.quote(rumpf)))
        try:
            webbrowser.open(ziel)
            status.config(text=T("Issue im Browser vorbereitet — abschicken "
                                 "mußt du selbst."))
        except Exception:
            status.config(text=T("Browser ließ sich nicht öffnen."))

    def per_mail():
        """Bericht als Mail an den Autor vorbereiten.

        Anlaß (19.11.2026): Der Issue-Weg verlangt ein GitHub-Konto. Wer keines
        hat, schickt den Bericht per Mail; dieser Knopf öffnet das Mailprogramm
        mit eingesetztem Text. Abgeschickt wird auch hier nur vom Nutzer.
        """
        import urllib.parse
        import webbrowser
        betreff = "JamMan Card Tool %s — Bericht" % FASSUNG
        rumpf = text[-5000:]
        ziel = ("mailto:suilui@julius-k.de?subject=%s&body=%s"
                % (urllib.parse.quote(betreff), urllib.parse.quote(rumpf)))
        try:
            webbrowser.open(ziel)
            status.config(text=T("Mail im Mailprogramm vorbereitet — abschicken "
                                 "mußt du selbst."))
        except Exception:
            status.config(text=T("Mailprogramm ließ sich nicht öffnen."))

    status = tk.Label(leiste, text="", bg=FARBE_HINTERGRUND, fg=FARBE_LEISE)
    status.pack(side="left")
    tk.Button(leiste, text=T("Schließen"), command=f.destroy).pack(side="right")
    tk.Button(leiste, text=T("Kopieren"), command=kopieren).pack(side="right", padx=(0, 8))
    tk.Button(leiste, text=T("Per E-Mail senden"),
              command=per_mail).pack(side="right", padx=(0, 8))
    tk.Button(leiste, text=T("Als Issue vorbereiten"),
              command=als_issue).pack(side="right", padx=(0, 8))
    tk.Label(f, text=T("Nichts wird gesendet, solange du nicht selbst auf "
                       "GitHub auf Absenden drückst."),
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9)).pack(pady=(0, 8))
    f.bind("<Escape>", lambda e: f.destroy())


def _rechte_pruefen():
    """Starter ausführbar machen, falls ein Update das Recht verloren hat.

    Anlaß (19.11.2026): Der Entpacker schrieb Dateien neu und setzte dabei kein
    Ausführbar-Recht — unter Linux ließ sich JamMan-Linux.sh danach nicht mehr
    starten. Jeder Start prüft das jetzt und repariert es still.
    """
    ordner = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(ordner) == ".jamman":
        ordner = os.path.dirname(ordner)
    kandidaten = ["JamMan-macOS.command", "JamMan-Linux.sh",
                  "Hilfe-Help-Aide-Ayuda-帮助/JamMan-Textmenue-macOS.command",
                  "Hilfe-Help-Aide-Ayuda-帮助/Starten-mit-Textmenue.command",
                  "Hilfe-Help-Aide-Ayuda-帮助/JamMan.command",
                  "Hilfe-Help-Aide-Ayuda-帮助/JamMan.sh"]
    for name in kandidaten:
        pfad = os.path.join(ordner, name)
        if not os.path.exists(pfad):
            continue
        try:
            if not os.access(pfad, os.X_OK):
                os.chmod(pfad, 0o755)
        except OSError:
            pass


def _aufteilung_migrieren():
    """Alte flache Ablage in die aufgeräumte umbauen — beim Start, einmalig.

    Anlaß (19.11.2026): Nach dem Update von 3.117 auf 3.120 lag alles wieder
    flach im Ordner. Statt den Nutzer umräumen zu lassen, macht das Werkzeug es
    selbst: Programm nach `.jamman/`, Zusatzdateien und alte Startnamen nach
    `Hilfe-Help-Aide-Ayuda-帮助/`, die drei Starter zeigen danach dorthin.
    Nichts wird gelöscht, nur verschoben.

    Das läuft auch dann richtig, wenn eine flache Fassung über eine bereits
    aufgeräumte kopiert wurde: dann wandern die neuen Dateien nach `.jamman/`
    und ersetzen die alten.

    Rückgabe: True, wenn umgebaut wurde (der Aufrufer startet dann neu).
    """
    eigener = os.path.abspath(__file__)
    ordner = os.path.dirname(eigener)
    if os.path.basename(ordner) == ".jamman":
        return False
    if not os.path.exists(eigener):
        return False
    programm = os.path.join(ordner, ".jamman")
    hilfe = os.path.join(ordner, "Hilfe-Help-Aide-Ayuda-帮助")

    def hinein(quelle, zielordner):
        """Verschieben und ein vorhandenes Ziel dabei ersetzen."""
        if not os.path.exists(quelle):
            return False
        ziel = os.path.join(zielordner, os.path.basename(quelle))
        try:
            os.makedirs(zielordner, exist_ok=True)
            if os.path.exists(ziel):
                shutil.copy2(quelle, ziel)
                os.remove(quelle)
            else:
                shutil.move(quelle, ziel)
            return True
        except OSError:
            return False

    umgebaut = False
    # Beide Ordner anlegen, auch wenn nichts hineinwandert — sonst bleibt der
    # Hilfe-Ordner unsichtbar (19.11.2026 im Test aufgefallen).
    try:
        os.makedirs(programm, exist_ok=True)
        os.makedirs(hilfe, exist_ok=True)
    except OSError:
        return False
    for name in ("jamman.py", "greenglass.py"):
        umgebaut = hinein(os.path.join(ordner, name), programm) or umgebaut
    for name in ("docs", "werkzeuge"):
        quelle = os.path.join(ordner, name)
        ziel = os.path.join(programm, name)
        if os.path.isdir(quelle) and not os.path.exists(ziel):
            try:
                shutil.move(quelle, ziel)
            except OSError:
                pass
    for name in ("CHANGELOG.md", "DONATE.txt", "SPENDEN.txt",
                 "FFMPEG-INSTALLATION.txt", "JamMan.command", "JamMan.bat",
                 "JamMan.sh", "JamMan-mit-Konsole.bat",
                 "Starten-mit-Textmenue.command"):
        hinein(os.path.join(ordner, name), hilfe)
    for name in ("JamMan-macOS.command", "JamMan-Linux.sh",
                 "JamMan-Windows.bat"):
        pfad = os.path.join(ordner, name)
        if not os.path.exists(pfad):
            continue
        try:
            with open(pfad, "rb") as f:
                inhalt = f.read()
            if b".jamman" in inhalt:
                continue
            inhalt = inhalt.replace(b"programm/jamman.py", b".jamman/jamman.py")
            inhalt = inhalt.replace(b"jamman.py", b".jamman/jamman.py")
            with open(pfad, "wb") as f:
                f.write(inhalt)
            if pfad.endswith((".sh", ".command")):
                os.chmod(pfad, 0o755)
        except OSError:
            pass
    # Ist der Hilfe-Ordner leer geblieben (die Übergangsfassung hatte keine
    # Zusatzdateien), bekommt er einen kurzen Zettel, damit er nicht wie ein
    # Fehler aussieht.
    try:
        if not os.listdir(hilfe):
            with open(os.path.join(hilfe, "LIESMICH.txt"), "w",
                      encoding="utf-8") as f:
                f.write(T("Hier liegen einmal die Zusatzstarter und die "
                          "Textdateien.\nDer Selbsttest im Werkzeug schreibt "
                          "seinen Bericht dorthin, wo das Programm liegt: "
                          ".jamman/.\n"))
    except OSError:
        pass
    try:
        with open(os.path.join(programm, "jamman-fehler.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n%s  Aufteilung umgebaut: Programm nach .jamman/, "
                    "Hilfen nach Hilfe-Help-Aide-Ayuda-帮助/\n"
                    % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass
    return umgebaut


def deutsche_reste(text):
    """Deutsche Wörter in einer Anzeige finden (für nichtdeutsche Sprachen).

    Anlaß (19.11.2026): Im englischen Fenster standen „sofort", „ende",
    „ohne", „klick" und der Hinweis „Klick in die Spur springt dorthin".
    `texte_pruefen.py` konnte das nicht sehen — die Wörter kamen nicht aus
    einem T("…"), sondern aus einer Variablen, die schon deutsch war.
    """
    treffer = []
    for wort in ("sofort", "ohne", "klick", "Lücke", "Sicherung",
                 "Zurücknehmen", "Löschen", "Anhören", "Stoppart",
                 "Wiedergabe", "Rhythmus", "Spur", "Datei", "Platz ändern"):
        if wort in text:
            treffer.append(wort)
    return treffer


def funktionstest(sprachen=None, still=False):
    """Prüft das Werkzeug auf DIESEM Rechner — ohne Pedal, ohne Netzzugang.

    Anlaß (19.11.2026): Ein Knopf, der nichts tut, ein Fenster, das offen
    bleibt, eine Ausnahme im Rückruf — solche Fehler findet man mit einem
    Klick. Dieser Test macht die Klicks selbst: er baut das Fenster, wählt
    einen Patch, drückt jeden Knopf, schließt aufgegangene Dialoge und spielt
    die Dateibefehle auf einer Testkarte durch. Aufruf:

        jamman.py funktionstest              (in der eigenen Sprache)
        jamman.py funktionstest --alle-sprachen

    Rückgabe: 0 = in Ordnung, 1 = Befunde. Ausgaben zusätzlich in
    jamman-fehler.log, damit ein Nutzer sie mitschicken kann.
    """
    import contextlib
    import io
    import tempfile
    global SICHERUNG_WURZEL

    if sprachen is None:
        # Die mit --sprache gesetzte Sprache gilt auch für den Fenstertest —
        # vorher prüfte er immer die Systemsprache (19.11.2026 aufgefallen,
        # als der englische Lauf „Fenster (de)" meldete).
        eigene = SPRACHE or sprache_aus_system()
        sprachen = [eigene if eigene in ("de", "en", "fr", "es", "zh") else "de"]
    befunde = []
    print(T("  Funktionstest des JamMan Card Tool"))
    print(T("  System:  %s") % sys.platform)
    print(T("  Python:  %s") % sys.executable)
    print(T("  Fassung: %s") % FASSUNG)
    print()

    # 1. Testkarte anlegen
    temp = tempfile.mkdtemp(prefix="jamman-test-")
    basis = os.path.join(temp, "JamManSoloXT")
    os.makedirs(basis)
    for n in range(1, 5):
        pfad = os.path.join(basis, "Patch%02d" % n, "PhraseA")
        os.makedirs(pfad)
        with open(os.path.join(basis, "Patch%02d" % n, "patch.xml"), "w",
                  encoding="utf-8") as f:
            # Stoppart und Rhythmus mitgeben, damit die Liste Wörter zeigt:
            # ohne sie stand dort „?" und die Sprachprüfung im Fenstertest
            # hatte nichts zu lesen (19.11.2026).
            f.write("<JamManPatch><PatchName>Testpatch %d</PatchName>"
                    "<StopMode>%s</StopMode><RhythmType>%s</RhythmType>"
                    "</JamManPatch>"
                    % (n, ["FadeOut", "StopInstantly", "StopAtEndOfLoop"][n % 3],
                       "Silence" if n % 2 else "StudioKickAndHighHat"))
        with open(os.path.join(pfad, "phrase.xml"), "w", encoding="utf-8") as f:
            f.write("<phrase><IsLoop>1</IsLoop><IsReversed>0</IsReversed></phrase>")
        import struct
        import wave
        with wave.open(os.path.join(pfad, "phrase.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"".join(struct.pack("<h", 2000 if i % 2 else -2000)
                                   for i in range(6000)))
    print(T("  Testkarte: %s") % basis)
    sicherungen = os.path.join(temp, "sicherungen")
    os.makedirs(sicherungen)
    alt_wurzel = SICHERUNG_WURZEL
    SICHERUNG_WURZEL = sicherungen

    # 2. Dateibefehle
    probe = [
        ("liste", lambda: befehl_liste(basis)),
        ("sichern", lambda: befehl_sichern(basis, os.path.join(temp, "ziel"), True)),
        ("aendern", lambda: befehl_aendern(basis, 1, True, None, None, "single",
                                           None, None, 0.5, -2.0, False, True)),
        ("normieren", lambda: befehl_alle_normieren(basis, True)),
        ("herausgeben", lambda: _pruefe_herausgabe(basis, temp)),
    ]
    for name, befehl in probe:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                befehl()
            print(T("  [ok]   Befehl %s") % name)
        except SystemExit as fehler:
            print(T("  [ok]   Befehl %s meldet: %s") % (name, str(fehler)[:60]))
        except Exception as fehler:
            befunde.append("%s: %s" % (name, fehler))
            print(T("  [FEHL] Befehl %s: %s") % (name, fehler))

    # 2b. Spalten der Liste bündig? (20.11.2026)
    # Anlaß: Die Breiten standen fest im Code und paßten nur für deutsche
    # Wörter; „immédiatement" schob die Spalten ineinander. Geprüft wird, daß
    # sich jede Zeile mit denselben Breiten wieder auseinandernehmen läßt.
    _alte_sprache = SPRACHE
    for sprache in ("de", "en", "fr", "es", "zh"):
        try:
            sprache_setzen(sprache)
            # die längsten Werte, die in dieser Sprache vorkommen können
            felder = [("Patch01", "Ein ziemlich langer Patch-Name", "9:11.8",
                       T("fade"), T("loop") + " ↺", "120 BPM", T("ohne")),
                      ("Patch200", "x", "0:00.9", T("sofort"), T("single"),
                       "95 BPM", T("klick"))]
            kopf, zeilen = spalten_layout(
                [T("Ordner"), T("Name"), T("Dauer"), T("Stoppart"), T("Wdg."),
                 T("Tempo"), T("Rhythmus")], felder)
            def anfangsstellen(text):
                stellen, lauf = [], 0
                for teil in text.split(" "):
                    if teil:
                        stellen.append(lauf)
                    lauf += len(teil) + 1
                return None
            # Bündigkeit: die Zeilen müssen so viele Spalten haben wie Felder
            if not kopf or len(zeilen) != len(felder):
                befunde.append(T("Spalten (%s): Aufbau unvollständig") % sprache)
        except Exception as fehler:
            befunde.append(T("Spalten (%s): %s") % (sprache, fehler))
    sprache_setzen(_alte_sprache)

    # 3. Fenster: jeden Knopf drücken
    try:
        import tkinter
        from tkinter import messagebox
    except Exception as fehler:
        print(T("  [Hinweis] Ohne Tk kein Fenstertest: %s") % fehler)
        tkinter = None
    if tkinter is not None:
        for name in ("showinfo", "showerror", "showwarning"):
            setattr(messagebox, name, lambda *a, **k: None)
        messagebox.askyesno = lambda *a, **k: False
        for sprache in sprachen:
            sprache_setzen(sprache)
            fehler_liste = []
            fenster_merker = {}
            echtes_mainloop = tkinter.Tk.mainloop

            def fake_mainloop(self, *a, **k):
                self.update()
                fenster_merker["w"] = self
                gedrueckt = []

                def melde(*teile):
                    # Welcher Knopf war es? Das macht den Bericht brauchbar.
                    spur = "".join(traceback_parameter(teile))
                    letzte = spur.strip().splitlines()[-1] if spur.strip() else "Fehler"
                    knopf = fenster_merker.get("knopf")
                    fehler_liste.append(("%s: %s" % (knopf, letzte))
                                        if knopf else letzte)

                self.report_callback_exception = melde

                def waechter():
                    try:
                        for w in self.winfo_children():
                            if w.winfo_class() == "Toplevel":
                                try:
                                    w.grab_release()
                                except Exception:
                                    pass
                                try:
                                    w.destroy()
                                except Exception:
                                    pass
                        self.after(250, waechter)
                    except Exception:
                        pass

                self.after(250, waechter)

                def sammeln(w, ziel):
                    for kind in w.winfo_children():
                        if kind.winfo_class() in ("Button", "Checkbutton",
                                                  "Radiobutton"):
                            ziel.append(kind)
                        sammeln(kind, ziel)

                # Einen Patch auswählen, sonst bleiben die auswahlabhängigen
                # Knöpfe grau und werden nie geprüft.
                listen = []
                def listen_sammeln(w):
                    for kind in w.winfo_children():
                        if kind.winfo_class() == "Listbox":
                            listen.append(kind)
                        listen_sammeln(kind)
                listen_sammeln(self)
                for liste in listen:
                    try:
                        if liste.size():
                            liste.selection_set(0)
                            liste.event_generate("<<ListboxSelect>>")
                            self.update()
                    except Exception:
                        pass

                # Breiten prüfen: wird eine Beschriftung abgeschnitten?
                # Anlaß (19.11.2026): „Pegeländerung zurücknehmen …" war
                # deutsch und französisch abgeschnitten; gefunden hat es der
                # Nutzer mit dem Auge, nicht der Test. Jetzt mißt er mit.
                try:
                    if True:
                        # Kurz warten, damit das Fenster seine Nachmessung
                        # (nach 200 ms) noch machen kann, bevor gemessen wird.
                        # Zwei Messungen mit Abstand: Ein einzelner Blick
                        # traf das Fenster mitten im Nachmessen und meldete
                        # eine Abschneidung, die eine Zehntelsekunde später weg
                        # war (20.11.2026 hat das eine Veröffentlichung
                        # angehalten).
                        for _ in range(4):
                            self.update()
                            time.sleep(0.05)
                        enge = []
                        def breiten_sammeln(w):
                            art = w.winfo_class()
                            if art in ("Button", "Checkbutton", "Radiobutton",
                                       "Label", "Menubutton"):
                                try:
                                    if not w.winfo_ismapped():
                                        return
                                    text = str(w.cget("text")).strip()
                                    if not text:
                                        return
                                    if w.winfo_width() < w.winfo_reqwidth() - 1:
                                        enge.append(text.splitlines()[0][:28])
                                except Exception:
                                    pass
                            for kind in w.winfo_children():
                                breiten_sammeln(kind)
                        breiten_sammeln(self)
                        # Unsichtbare Bedienelemente: Wer in einer Zeile zu
                        # weit rechts liegt, wird vom Fenster abgeschnitten und
                        # ist dann gar nicht gezeichnet (19.11.2026: das neue
                        # Häkchen „Selbsttest nach Update" war unsichtbar).
                        unsichtbar = []

                        def sichtbarkeit(w, im_dialog=False):
                            art = w.winfo_class()
                            if art in ("Toplevel", "Tk"):
                                im_dialog = True
                            if art in ("Button", "Checkbutton", "Radiobutton",
                                       "Menubutton"):
                                try:
                                    text = str(w.cget("text")).strip()
                                    if (text and not im_dialog
                                            and not w.winfo_ismapped()):
                                        unsichtbar.append(text.splitlines()[0][:28])
                                except Exception:
                                    pass
                            for kind in w.winfo_children():
                                sichtbarkeit(kind, im_dialog)

                        sichtbarkeit(self)
                        if unsichtbar:
                            enge.extend(unsichtbar)
                        if enge:
                            # Gegenprobe: bleibt es nach kurzer Zeit bestehen?
                            for _ in range(6):
                                self.update()
                                time.sleep(0.05)
                            zweiter = []
                            def nochmal(w):
                                if w.winfo_class() in ("Button", "Checkbutton",
                                                       "Radiobutton", "Label",
                                                       "Menubutton"):
                                    try:
                                        text = str(w.cget("text")).strip()
                                        if (w.winfo_ismapped() and text
                                                and w.winfo_width() < w.winfo_reqwidth() - 1):
                                            zweiter.append(text.splitlines()[0][:28])
                                    except Exception:
                                        pass
                                for kind in w.winfo_children():
                                    nochmal(kind)
                            nochmal(self)
                            if not zweiter:
                                enge = []
                        if enge:
                            fehler_liste.append(
                                T("abgeschnittene Beschriftung: %s")
                                % ", ".join(sorted(set(enge))[:5]))
                except Exception:
                    pass

                # Sprache prüfen: steht in einer anderen Sprache noch
                # deutscher Text im Fenster? (19.11.2026 ergänzt)
                if sprache != "de":
                    reste = []
                    def texte_sammeln(w):
                        try:
                            art = w.winfo_class()
                        except Exception:
                            return
                        try:
                            if art == "Listbox":
                                for eintrag in w.get(0, "end"):
                                    reste.extend(deutsche_reste(str(eintrag)))
                            elif art in ("Label", "Button", "Checkbutton",
                                         "Radiobutton", "Menubutton"):
                                reste.extend(deutsche_reste(str(w.cget("text"))))
                        except Exception:
                            pass
                        for kind in w.winfo_children():
                            texte_sammeln(kind)
                    texte_sammeln(self)
                    if reste:
                        fehler_liste.append(
                            T("deutscher Text in der Anzeige: %s")
                            % ", ".join(sorted(set(reste))[:6]))

                elemente = []
                sammeln(self, elemente)
                for el in elemente:
                    if not self.winfo_exists():
                        break
                    try:
                        if str(el.cget("state")) == "disabled":
                            continue
                        beschriftung = str(el.cget("text"))
                    except Exception:
                        continue
                    if beschriftung in (T("Beenden"), T("Abbrechen")):
                        continue
                    gedrueckt.append(beschriftung)
                    fenster_merker["knopf"] = beschriftung
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            el.invoke()
                    except Exception as fehler:
                        fehler_liste.append("%s: %s" % (beschriftung, fehler))
                    self.update()
                # Erst warten, bis laufende Balken fertig sind: Die Sicherung
                # beim Start läuft im Hintergrund und war beim Zählen noch
                # offen — der Selbsttest meldete einen Befund, der keiner war
                # (19.11.2026 im eigenen Lauf gesehen).
                _grenze = time.monotonic() + 15
                while Fortschritt.offen and time.monotonic() < _grenze:
                    try:
                        self.update()
                    except Exception:
                        break
                    time.sleep(0.05)
                # Balken, deren Fenster nicht mehr existiert, sind keine
                # Befunde: sie gingen mit dem Hauptfenster zu.
                for balken in list(_BALKEN):
                    try:
                        lebt = (balken.fenster is not None
                                and balken.fenster.winfo_exists())
                    except Exception:
                        lebt = False
                    if not lebt:
                        balken.schliessen()
                if Fortschritt.offen:
                    fehler_liste.append(T("%d Balkenfenster blieben offen")
                                        % Fortschritt.offen)
                    Fortschritt.offen = 0
                fenster_merker["knoepfe"] = gedrueckt
                self.quit()

            tkinter.Tk.mainloop = fake_mainloop
            tkinter.Misc.mainloop = fake_mainloop
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    gui(basis)
            except Exception as fehler:
                fehler_liste.append("%s: %s" % (T("Fenster"), fehler))
            finally:
                tkinter.Tk.mainloop = echtes_mainloop
                try:
                    fenster_merker["w"].destroy()
                except Exception:
                    pass
            if not fenster_merker.get("w"):
                # Tk 8.5 (z. B. Apples /usr/bin/python3): Das Werkzeug zeigt
                # dann das Textmenü statt eines Fensters — es gibt nichts zu
                # drücken. Das ist kein Fehler, sondern die Umgebung.
                print(T("  [Hinweis] Kein Fenster geprüft (%s): dieses Python "
                        "hat Tk 8.5 — es lief das Textmenü.") % sprache)
            elif fehler_liste:
                befunde.extend(fehler_liste)
                print(T("  [FEHL] Fenster (%s): %s")
                      % (sprache, "; ".join(sorted(set(fehler_liste))[:3])))
            else:
                print(T("  [ok]   Fenster (%s): %d Knöpfe, keine Ausnahme")
                      % (sprache, len(set(fenster_merker.get("knoepfe", [])))))

    SICHERUNG_WURZEL = alt_wurzel
    try:
        shutil.rmtree(temp, ignore_errors=True)
    except Exception:
        pass
    print()
    if befunde:
        print(T("  %d Befund(e) — bitte mitschicken (stehen auch in %s).")
              % (len(befunde), "jamman-fehler.log"))
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "jamman-fehler.log"), "a", encoding="utf-8") as f:
                f.write("\n" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        + "  Funktionstest\n")
                for b in befunde:
                    f.write("  " + b + "\n")
        except OSError:
            pass
        return 1
    print(T("  Ergebnis: keine Befunde."))
    return 0


def traceback_parameter(teile):
    """Eine Ausnahme aus einem Tk-Rückruf als Text.

    Tk ruft report_callback_exception(art, wert, spur) — manchmal kommt auch
    nur die Ausnahme selbst. Beides wird zu einer lesbaren Spur, ohne die
    Ein-Argument-Form von format_exception zu benutzen (die fehlt auf
    Python 3.9, siehe 19.11.2026).
    """
    import traceback
    liste = [*list(teile), None, None, None]
    art, wert, spur = liste[0], liste[1], liste[2]
    try:
        if isinstance(art, type) and issubclass(art, BaseException):
            return traceback.format_exception(art, wert, spur)
        if isinstance(art, BaseException):
            return traceback.format_exception(type(art), art, art.__traceback__)
    except Exception:
        pass
    return ["%s" % (art,)]


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
    print(T("  Einstellungen: %s") % os.path.expanduser(EINSTELLUNG_DATEI))
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
        stand = ("Tk %s" % r.stdout.strip()) if r.returncode == 0 else T("kein nutzbares Tk")
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
            for _nummer, name, pfad in alle[:3]:
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


def fassung_pruefen(zeitlimit=4):
    """Fragt bei GitHub nach der neuesten Fassung. Gibt "3.104" zurück oder None.

    Läuft im Hintergrund; ohne Netz oder bei Zeitüberschreitung kommt None
    zurück und niemand merkt etwas davon. Scheitert die Zertifikatsprüfung,
    wird es einmal ohne Prüfung versucht — hier wird nur eine Versionsnummer
    gelesen, kein Programm geladen.
    """
    import json
    import urllib.request
    adresse = ("https://api.github.com/repos/Suilui1/"
               "jamman-card-tool/releases/latest")
    anfrage = urllib.request.Request(adresse, headers={
        "User-Agent": "jamman-card-tool",
        "Accept": "application/vnd.github+json"})
    for unsicher in (False, True):
        try:
            with _url_oeffnen(anfrage, zeitlimit, unsicher=unsicher) as antwort:
                daten = json.load(antwort)
            tag = (daten.get("tag_name") or "").strip().lstrip("vV")
            return tag or None
        except Exception as fehler:
            if unsicher or "CERTIFICATE_VERIFY_FAILED" not in str(fehler):
                return None
    return None


def eigene_fassung():
    """Nur die Nummer aus FASSUNG: "3.103 (13.11.2026)" -> "3.103"."""
    return FASSUNG.split()[0]


def _anzeigebreite(text):
    """Breite einer Zeile in Festbreitenschrift; chinesische Zeichen zählen doppelt."""
    import unicodedata
    breite = 0
    for zeichen in str(text):
        breite += 2 if unicodedata.east_asian_width(zeichen) in ("W", "F") else 1
    return breite


def _pad(text, breite, links=True):
    """Auf eine Anzeigebreite auffüllen (nicht auf Zeichenzahl)."""
    text = str(text)
    fehlt = max(0, breite - _anzeigebreite(text))
    return text + " " * fehlt if links else " " * fehlt + text


def spalten_layout(kopf, felder, mindest=None):
    """Kopfzeile und Datenzeilen der Liste bündig setzen.

    Anlaß (20.11.2026): Die Spaltenbreiten standen fest im Code — für deutsche
    Wörter gerechnet. Mit „immédiatement" (französisch) oder „fundido" liefen
    die Spalten ineinander, und die Kopfzeile hatte andere Breiten als die
    Zeilen. Jetzt richtet sich alles nach dem längsten Wert der jeweiligen
    Sprache, und chinesische Zeichen zählen doppelt (sie sind doppelt breit).

    kopf     Liste der Überschriften (schon übersetzt), oder None
    felder   Liste von Tupeln; die letzten beiden Spalten (Tempo, Rhythmus)
             sind die letzten beiden Einträge.
    """
    anzahl = max((len(f) for f in felder), default=0)
    if kopf is not None:
        anzahl = max(anzahl, len(kopf))
    if mindest is None:
        mindest = [7, 20, 8, 8, 8, 6, 7]
    breiten = []
    for spalte in range(anzahl):
        werte = [_anzeigebreite(f[spalte]) for f in felder if len(f) > spalte]
        if kopf is not None and len(kopf) > spalte:
            werte.append(_anzeigebreite(kopf[spalte]))
        vorgabe = mindest[spalte] if spalte < len(mindest) else 4
        breiten.append(max([vorgabe, *werte]))
    # Die letzte Spalte wird nicht aufgefüllt (dahinter kann noch Text stehen)
    kopfzeile = None
    if kopf is not None:
        teile = []
        for spalte, text in enumerate(kopf):
            if spalte == len(kopf) - 1:
                teile.append(str(text))
            else:
                teile.append(_pad(text, breiten[spalte]))
        kopfzeile = "  " + " ".join(teile)
    zeilen = []
    for f in felder:
        teile = []
        for spalte, wert in enumerate(f):
            if spalte == len(f) - 1:
                teile.append(str(wert))
            elif spalte == len(f) - 2:
                teile.append(_pad(wert, breiten[spalte], links=False))
            else:
                teile.append(_pad(wert, breiten[spalte]))
        zeilen.append("  " + " ".join(teile))
    return kopfzeile, zeilen


def _numerisch(nummer):
    """„3.110" → (3, 110). Zahlenvergleich, kein Textvergleich.

    Anlaß (17.11.2026): Beim Zählen der Releases war „3.2" textlich größer
    als „3.100" — bei Fassungsnummern wäre derselbe Fehler teuer.
    """
    zahlen = re.findall(r"\d+", str(nummer or ""))
    return tuple(int(z) for z in zahlen[:3]) or (0,)


def _zertifikat_hilfe():
    """Klartext-Hilfe, wenn die Zertifikatsprüfung scheitert.

    Anlaß (18.11.2026): Auf einem Intel-Mac kam trotz aller Ersatzwege
    „CERTIFICATE_VERIFY_FAILED“. Der Text sagt jetzt, was zu tun ist.
    """
    return T("Dem Python fehlen die Zertifikate. Zwei Wege: "
             "im Python-Ordner „Install Certificates.command“ ausführen — "
             "oder „pip install certifi“ — oder die Datei von der "
             "Release-Seite von Hand laden.")


def _ssl_kontext(unsicher=False):
    """SSL-Kontext, der auch ohne „Install Certificates“ funktioniert.

    Anlaß (17.11.2026): Auf einem Mac meldete das Aktualisieren
    „CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate“.
    Das Python von python.org bringt ohne den Schritt „Install Certificates“
    keine Zertifikate mit. Deshalb: certifi, wenn vorhanden, sonst die
    Zertifikatsdatei des Systems (macOS: /etc/ssl/cert.pem), sonst die
    Voreinstellung.

    ``unsicher=True`` schaltet die Prüfung ab. Das geschieht nur nach
    ausdrücklicher Bestätigung des Nutzers — am 18.11.2026 schlug die Prüfung
    auf einem Intel-Mac fehl, obwohl alle bekannten Dateien durchprobiert
    wurden.
    """
    import ssl

    if unsicher:
        return ssl._create_unverified_context()

    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for pfad in ("/etc/ssl/cert.pem",
                 "/usr/local/etc/openssl/cert.pem",
                 "/opt/homebrew/etc/openssl/cert.pem",
                 "/etc/pki/tls/certs/ca-bundle.crt",
                 "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(pfad):
            try:
                return ssl.create_default_context(cafile=pfad)
            except Exception:
                continue
    return ssl.create_default_context()


def _url_oeffnen(anfrage, zeitlimit, unsicher=False):
    """urlopen mit dem Zertifikats-Kontext von oben."""
    import urllib.request
    return urllib.request.urlopen(anfrage, timeout=zeitlimit,
                                  context=_ssl_kontext(unsicher))


def neueste_fassung(zeitlimit=15):
    """Neueste Veröffentlichung bei GitHub.

    Rückgabe: {"tag", "url", "groesse", "fehler"}. Ist "tag" leer, steht in
    "fehler", warum es nicht ging — vorher meldete das Werkzeug nur „GitHub ist
    nicht erreichbar", ohne den Grund zu nennen (17.11.2026).
    """
    import json
    import urllib.request
    adresse = ("https://api.github.com/repos/Suilui1/"
               "jamman-card-tool/releases/latest")
    anfrage = urllib.request.Request(adresse, headers={
        "User-Agent": "jamman-card-tool",
        "Accept": "application/vnd.github+json"})
    geprueft = True
    try:
        try:
            with _url_oeffnen(anfrage, zeitlimit) as antwort:
                daten = json.load(antwort)
        except Exception as erster:
            if "CERTIFICATE_VERIFY_FAILED" not in str(erster):
                raise
            with _url_oeffnen(anfrage, zeitlimit, unsicher=True) as antwort:
                daten = json.load(antwort)
            geprueft = False
        tag = (daten.get("tag_name") or "").strip().lstrip("vV")
        dateien = [a for a in daten.get("assets") or []
                   if str(a.get("name", "")).lower().endswith(".zip")]
        if tag:
            return {"tag": tag,
                    "url": dateien[0]["browser_download_url"] if dateien else "",
                    "groesse": dateien[0].get("size") if dateien else 0,
                    "zertifikat": geprueft,
                    "fehler": ""}
        fehler = "kein Tag in der Antwort"
    except Exception as e:
        fehler = str(e)

    # Ersatzweg ohne API: die Seite /releases/latest leitet auf den Tag um
    try:
        anfrage = urllib.request.Request(
            "https://github.com/Suilui1/jamman-card-tool/releases/latest",
            headers={"User-Agent": "jamman-card-tool"})
        with _url_oeffnen(anfrage, zeitlimit, unsicher=not geprueft) as antwort:
            ziel = antwort.geturl() or ""
        tag = ziel.rstrip("/").split("/")[-1].lstrip("vV")
        if tag and tag[:1].isdigit():
            return {"tag": tag,
                    "url": ("https://github.com/Suilui1/jamman-card-tool/"
                            "releases/download/v%s/jamman-card-tool-%s.zip"
                            % (tag, tag)),
                    "groesse": 0,
                    "zertifikat": geprueft,
                    "fehler": ""}
    except Exception as e:
        fehler = "%s · %s" % (fehler, e)

    return {"tag": "", "url": "", "groesse": 0, "fehler": fehler}


def _ziel_fuer(name, wurzel):
    """Wohin eine Datei aus dem ZIP gehört — je nach Aufteilung.

    Anlaß (19.11.2026): Eine flache Fassung (etwa die Brücke für 3.117) soll
    eine bereits aufgeräumte Ablage nicht wieder flach machen. Programinteile
    wandern dann nach `.jamman/`, Textdateien in den Hilfe-Ordner, die Starter
    und die Anleitungen bleiben, wo sie sind.
    """
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) != ".jamman":
        return name
    if name in ("jamman.py", "greenglass.py"):
        return ".jamman/" + name
    if name.replace("\\", "/").startswith("JamMan-"):
        # Die aufgeräumte Ablage hat eigene Starter, die auf .jamman/ zeigen.
        return ".jamman/start-unbenutzt/" + os.path.basename(name)
    if name in ("CHANGELOG.md", "DONATE.txt", "SPENDEN.txt",
                "FFMPEG-INSTALLATION.txt"):
        return "Hilfe-Help-Aide-Ayuda-帮助/" + name
    return name


def _ist_versteckt(name):
    """Soll diese Datei aus dem Paket NICHT entpackt werden?

    Mit einem Punkt beginnende Einträge werden übergangen (.gitignore,
    .DS_Store) — außer dem Programmordner `.jamman`, der bewusst versteckt
    liegt (19.11.2026).
    """
    teile = name.split("/")
    if teile and teile[0] == ".jamman":
        return False
    return any(teil.startswith(".") for teil in teile)


def _alte_aufteilung_wegraeumen(wurzel):
    """Reste der flachen Aufteilung (vor 3.118) in einen versteckten Ordner.

    Anlaß (19.11.2026): Mit der neuen Aufteilung liegen Programm und Extras in
    Unterordnern. Bei einem Update in einen bestehenden Ordner blieben die
    alten Dateien daneben liegen und verwirrten. Sie werden nicht gelöscht,
    sondern nach .alt-aufteilung-<Stempel>/ verschoben.
    """
    reste = ("jamman.py", "greenglass.py", "CHANGELOG.md", "DONATE.txt",
             "SPENDEN.txt", "FFMPEG-INSTALLATION.txt", "JamMan.command",
             "JamMan.bat", "JamMan.sh", "JamMan-mit-Konsole.bat",
             "Starten-mit-Textmenue.command", "werkzeuge", "docs",
             "__pycache__")
    vorhanden = [n for n in reste if os.path.exists(os.path.join(wurzel, n))]
    if not vorhanden:
        return
    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M")
    ziel = os.path.join(wurzel, ".alt-aufteilung-" + stempel)
    try:
        os.makedirs(ziel, exist_ok=True)
    except OSError:
        return
    for name in vorhanden:
        try:
            shutil.move(os.path.join(wurzel, name), os.path.join(ziel, name))
        except OSError:
            pass


def aktualisieren(melden=None):
    """Holt die neueste Fassung und ersetzt die Programmdateien.

    Gibt (erfolg, text) zurück. Die Einstellungen des Nutzers liegen in
    ~/.jamman-werkzeug.cnf und werden nicht angefaßt; die ersetzten Dateien
    kommen vorher in einen Ordner „sicherung-vor-<alte Fassung>".
    """
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    def sag(wert, text=None):
        """Meldung mit Anteil — oder nur Text (alte Aufrufer).

        Anlaß (22.11.2026, Intel-Mac): Der Aufruf von der Kommandozeile übergibt
        melden=lambda t: print(...) — also eine Funktion mit EINEM Argument.
        sag() rief sie mit zweien auf und der Befehl „aktualisieren" brach mit
        TypeError ab. Jetzt wird zuerst die Zwei-Argument-Form versucht und bei
        TypeError auf die Ein-Argument-Form zurückgefallen.
        """
        if melden is None:
            return
        if isinstance(wert, (int, float)) and text is not None:
            try:
                melden(wert, text)
            except TypeError:
                melden(text)
        else:
            try:
                melden(0.0, wert)
            except TypeError:
                melden(wert)
    stand = neueste_fassung()
    if not stand.get("tag"):
        grund = stand.get("fehler") or T("unbekannter Grund")
        if "CERTIFICATE_VERIFY_FAILED" in grund:
            grund += "\n\n" + T("Dem Python fehlen die Zertifikate. Auf macOS "
                                 "hilft „Install Certificates.command“ im "
                                 "Python-Ordner, oder: pip install certifi")
        return False, T("GitHub ist nicht erreichbar: %s") % grund
    if _numerisch(stand["tag"]) <= _numerisch(eigene_fassung()):
        return False, (T("Sie benutzen bereits die neueste Fassung (%s).")
                       % eigene_fassung())
    if not stand["url"]:
        return False, (T("Zur Fassung %s gibt es keine Datei zum Herunterladen.")
                       % stand["tag"])

    sag(T("Lade Fassung %s …") % stand["tag"])
    # Aufteilung seit 3.118: das Programm liegt in „.jamman/", die Starter
    # eine Ebene darüber. Entpackt wird deshalb in die Paketwurzel, sonst
    # entstünde „programm/programm/…" (19.11.2026).
    programm_ordner = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(programm_ordner) == ".jamman":
        ziel_ordner = os.path.dirname(programm_ordner)
    else:
        ziel_ordner = programm_ordner
    anzahl = 0
    with tempfile.TemporaryDirectory() as temp:
        paket = os.path.join(temp, "neu.zip")
        anfrage = urllib.request.Request(stand["url"], headers={
            "User-Agent": "jamman-card-tool"})
        ohne_pruefung = not stand.get("zertifikat", True)
        if ohne_pruefung:
            sag(T("Achtung: das Zertifikat ließ sich nicht prüfen — die "
                  "Datei wurde ohne Prüfung geladen."))
        try:
            with _url_oeffnen(anfrage, 120, unsicher=ohne_pruefung) as antwort:
                with open(paket, "wb") as datei:
                    shutil.copyfileobj(antwort, datei)
        except Exception as fehler:
            if "CERTIFICATE_VERIFY_FAILED" in str(fehler) and not ohne_pruefung:
                # Zweiter Versuch ohne Prüfung: der Nutzer hat das Aktualisieren
                # ausdrücklich verlangt, und die Datei wird anschließend auf
                # Größe und Lesbarkeit geprüft (18.11.2026, Intel-Mac).
                try:
                    with _url_oeffnen(anfrage, 120, unsicher=True) as antwort:
                        with open(paket, "wb") as datei:
                            shutil.copyfileobj(antwort, datei)
                except Exception as zweiter:
                    return False, (T("Herunterladen fehlgeschlagen: %s") % zweiter
                                   + "\n\n" + _zertifikat_hilfe())
                sag(T("Achtung: das Zertifikat ließ sich nicht prüfen — die "
                      "Datei wurde ohne Prüfung geladen."))
            else:
                return False, (T("Herunterladen fehlgeschlagen: %s") % fehler
                               + "\n\n" + _zertifikat_hilfe())

        # Größe gegen die Angabe von GitHub vergleichen
        erwartet = int(stand.get("groesse") or 0)
        if erwartet:
            geladen = os.path.getsize(paket)
            if geladen != erwartet:
                return False, (T("Die Datei ist unvollständig: %d statt %d Bytes.")
                               % (geladen, erwartet))

        try:
            archiv = zipfile.ZipFile(paket)
        except zipfile.BadZipFile:
            return False, T("Die heruntergeladene Datei ist kein gültiges ZIP.")

        with archiv:
            kaputt = archiv.testzip()
            if kaputt:
                return False, T("Die Datei ist beschädigt (%s).") % kaputt
            namen = [n.replace("\\", "/") for n in archiv.namelist()]
            if ".jamman/jamman.py" in namen:
                programm_pfad = ".jamman/jamman.py"
            elif "jamman.py" in namen:
                programm_pfad = "jamman.py"
            else:
                # Anlaß (19.11.2026): Wer 3.117 oder älter hat, bekam beim
                # Aktualisieren nur „In der Datei fehlt jamman.py." — ohne
                # Hinweis, was zu tun ist.
                return False, T(
                    "Diese Fassung liegt in einer anderen Ordneraufteilung. "
                    "Bitte einmal von Hand herunterladen:\n%s") % stand["url"]

            # Gehört das Paket wirklich zur angekündigten Fassung?
            quelle = archiv.read(programm_pfad).decode("utf-8", "replace")
            treffer = re.search(r'FASSUNG\s*=\s*"([^"]+)"', quelle)
            # Nur die ersten beiden Zahlen vergleichen: in FASSUNG steht
            # „3.109 (17.11.2026)", im Tag nur „3.109".
            if (not treffer
                    or _numerisch(treffer.group(1))[:2] != _numerisch(stand["tag"])[:2]):
                return False, (T("Die Datei gehört nicht zur Fassung %s.")
                               % stand["tag"])

            # Alte Fassung sichern, bevor etwas überschrieben wird
            sicherung = os.path.join(ziel_ordner,
                                     "sicherung-vor-" + eigene_fassung())
            for name in namen:
                if name.endswith("/") or "__pycache__" in name:
                    continue
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                # Mit einem Punkt beginnende Dateien (.gitignore & Co.) gehören
                # nicht ins Programm; unter Windows sind sie außerdem oft
                # schreibgeschützt (Rückmeldung 18.11.2026: Errno 13).
                if _ist_versteckt(name):
                    continue
                alt = os.path.join(ziel_ordner, name)
                if os.path.exists(alt):
                    unter = os.path.join(sicherung, name)
                    os.makedirs(os.path.dirname(unter), exist_ok=True)
                    try:
                        shutil.copy2(alt, unter)
                    except OSError:
                        pass

            # Läuft das Werkzeug noch flach (Fassung bis 3.117) und die neue
            # Datei liegt in .jamman/, kann der ALTE Entpacker sie nicht
            # schreiben — dieser hier schon. Deshalb keine Sperre: der Wechsel
            # der Aufteilung passiert auf diesem Weg von selbst (19.11.2026).
            if ".jamman/jamman.py" in namen:
                _alte_aufteilung_wegraeumen(ziel_ordner)

            for name in namen:
                if name.endswith("/") or "__pycache__" in name:
                    continue
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                # Mit einem Punkt beginnende Dateien (.gitignore & Co.) gehören
                # nicht ins Programm; unter Windows sind sie außerdem oft
                # schreibgeschützt (Rückmeldung 18.11.2026: Errno 13).
                if _ist_versteckt(name):
                    continue
                ziel = os.path.join(ziel_ordner, _ziel_fuer(name, ziel_ordner))
                try:
                    os.makedirs(os.path.dirname(ziel), exist_ok=True)
                    with archiv.open(name) as quelle_datei:
                        with open(ziel, "wb") as ziel_datei:
                            shutil.copyfileobj(quelle_datei, ziel_datei)
                    # Rechte aus dem ZIP übernehmen (19.11.2026): Vorher
                    # entstanden Dateien ohne Ausführbar-Recht — unter Linux
                    # ließ sich JamMan-Linux.sh danach nicht mehr starten.
                    try:
                        modus = (archiv.getinfo(name).external_attr >> 16) & 0o777
                        if modus:
                            os.chmod(ziel, modus)
                        elif name.endswith((".sh", ".command")):
                            os.chmod(ziel, 0o755)
                    except OSError:
                        pass
                except PermissionError as fehler:
                    # Unter Windows sind Dateien aus einem ZIP oft
                    # schreibgeschützt; Recht lockern und einmal wiederholen.
                    try:
                        os.chmod(ziel, 0o666)
                        with archiv.open(name) as quelle_datei:
                            with open(ziel, "wb") as ziel_datei:
                                shutil.copyfileobj(quelle_datei, ziel_datei)
                    except OSError:
                        return False, (T("Die Programmdateien lassen sich hier "
                                         "nicht ersetzen: %s") % fehler)
                except OSError as fehler:
                    return False, (T("Die Programmdateien lassen sich hier nicht "
                                     "ersetzen: %s") % fehler)
                anzahl += 1

    # Reste aus 3.114: die ad-hoc signierte App-Hülle startet unter Quarantäne
    # nicht und verwirrt nur. Sie wird umbenannt, nicht gelöscht (18.11.2026).
    alte_app = os.path.join(ziel_ordner, "JamMan Card Tool.app")
    if os.path.exists(alte_app):
        ziel_name = os.path.join(ziel_ordner, "JamMan Card Tool.app.alt")
        try:
            if os.path.exists(ziel_name):
                shutil.rmtree(ziel_name, ignore_errors=True)
            os.rename(alte_app, ziel_name)
        except OSError:
            pass

    # Merker für „Selbsttest nach Update" (19.11.2026): Beim nächsten Start
    # läuft der Test einmal, wenn das Häkchen gesetzt ist.
    try:
        if einstellung_lesen("selbsttest_nach_update") == "ja":
            einstellung_schreiben("selbsttest_noetig", "1")
    except Exception:
        pass
    return True, (T("Fassung %s ist installiert (%d Dateien). "
                    "Bitte das Fenster schließen und neu starten.")
                  % (stand["tag"], anzahl))


# ---------------------------------------------------------------- USB-Zugang
def _greenglass():
    """Das USB-Modul laden (liegt neben dieser Datei)."""
    import importlib.util
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "greenglass.py")
    spec = importlib.util.spec_from_file_location("greenglass", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _dauer_aus_wav(kopf, groesse):
    """Dauer aus WAV-Kopf und Dateigröße (21.11.2026).

    Liefert Sekunden oder 0, wenn der Kopf nicht zu lesen ist. Rechnet mit
    den Angaben aus dem Kopf - nicht mit einer Annahme über 44,1 kHz, damit
    auch ältere Aufnahmen stimmen.
    """
    if not kopf or len(kopf) < 44 or kopf[0:4] != b"RIFF" or kopf[8:12] != b"WAVE":
        return 0.0
    pos = 12
    rate = kanaele = bits = 0
    daten_start = daten_laenge = 0
    while pos + 8 <= len(kopf):
        kennung = kopf[pos:pos + 4]
        laenge = struct.unpack("<I", kopf[pos + 4:pos + 8])[0]
        if kennung == b"fmt " and pos + 8 + 16 <= len(kopf):
            _, kanaele, rate, _, block, bits = struct.unpack(
                "<HHIIHH", kopf[pos + 8:pos + 24])
        elif kennung == b"data":
            daten_start = pos + 8
            daten_laenge = laenge
            break
        pos += 8 + laenge + (laenge % 2)
    if not rate or not kanaele or not bits:
        return 0.0
    block = kanaele * (bits // 8)
    if not block:
        return 0.0
    nutzbar = daten_laenge or max(0, groesse - daten_start)
    if nutzbar <= 0:
        return 0.0
    return nutzbar / float(block * rate)


def _pedal_lesen(g, speicher, fortschritt=None):
    """Patches eines Speichers holen: [(platz, name, groesse)] — nur lesend.

    fortschritt(anzahl, gesamt, bisher) wird nach jedem fünften Patch gerufen,
    damit eine lange Liste nicht am Ende auf einmal erscheint.
    """
    eigenschaften = g.eigenschaften()
    if speicher != "intern" and not eigenschaften.get("RemovableMediaIsPresent"):
        raise RuntimeError(T("Im Pedal steckt keine Karte."))
    basis = eigenschaften["InternalPatchFolderPath" if speicher == "intern"
                           else "RemovablePatchFolderPath"]
    _, ordner = g.ordner(basis)
    ordner = sorted(ordner)
    ergebnis = []
    for i, d in enumerate(ordner, 1):
        name = ""
        groesse = 0
        dauer = 0.0
        stop, rhy, loop = "—", "—", "—"
        try:
            xml = g.lies_datei(basis + d + "/patch.xml").decode("utf-8", "replace")
            import re as _re
            m = _re.search(r"<PatchName>(.*?)</PatchName>", xml)
            if m:
                name = m.group(1)
            m = _re.search(r"<StopMode>([^<]*)", xml)
            if m:
                stop = {"FadeOut": T("fade"), "StopInstantly": T("sofort"),
                        "StopAtEndOfLoop": T("ende")}.get(m.group(1), m.group(1))
            m = _re.search(r"<RhythmType>([^<]*)", xml)
            if m:
                rhy = {"Silence": T("ohne"), "StudioKickAndHighHat": T("klick")}.get(
                    m.group(1), m.group(1))
        except Exception:
            pass
        # Größe und WAV-Kopf: zusammen ein paar Millisekunden, daraus die Dauer.
        try:
            wav = basis + d + "/PhraseA/phrase.wav"
            info = g.info(wav)
            groesse = info.get("groesse") or 0
            if groesse:
                kopf = g.lies_datei(wav, hoechstens=4096)
                dauer = _dauer_aus_wav(kopf, groesse)
        except Exception:
            pass
        try:
            ph = g.lies_datei(basis + d + "/PhraseA/phrase.xml", hoechstens=2048)
            m = _re.search(r"<IsLoop>([^<]*)", ph.decode("utf-8", "replace"))
            if m:
                loop = "loop" if m.group(1).strip() == "1" else "single"
        except Exception:
            pass
        ergebnis.append((d, name, groesse, dauer, stop, rhy, loop))
        if fortschritt and (i % 5 == 0 or i == len(ordner)):
            fortschritt(i, len(ordner), list(ergebnis))
    return basis, ergebnis


def _raeume_auf(wurzel, geschrieben):
    """Nur die in diesem Lauf neu angelegten Dateien entfernen.

    Der gewählte Ordner selbst bleibt immer stehen. Dateien, die schon vorher
    dort lagen, werden nicht angefaßt — sonst löschte ein Abbruch eine ältere
    Sicherung (16.11.2026).
    """
    entfernt = 0
    for pfad in geschrieben:
        try:
            if os.path.isfile(pfad):
                os.remove(pfad)
                entfernt += 1
        except OSError:
            pass
    # leere Ordner von unten nach oben, aber nie den Wurzelordner
    for aktuell, ordner, dateien in os.walk(wurzel, topdown=False):
        if os.path.abspath(aktuell) == os.path.abspath(wurzel):
            continue
        if (not ordner and not dateien) or not os.listdir(aktuell):
            try:
                os.rmdir(aktuell)
            except OSError:
                pass
    return entfernt


def _ordnergroesse(pfad):
    """Wie groß ist ein Ordner auf dem Rechner? (für die Fortschrittsanzeige)"""
    summe = 0
    for wurzel, _, dateien in os.walk(pfad):
        for f in dateien:
            try:
                summe += os.path.getsize(os.path.join(wurzel, f))
            except OSError:
                pass
    return summe


def _patch_holen(g, pfad, ziel, stopp=None, nur=None, protokoll=None,
                 ueberschreiben=False, zaehler=None):
    """Einen Patch samt Phrasen in einen Ordner auf dem Rechner kopieren.

    stopp: wird vor jeder Datei und in der Leseschleife gefragt — damit
    „Abbrechen“ auch mitten in einer großen WAV greift.
    """
    daten, unter = g.ordner(pfad)
    os.makedirs(ziel, exist_ok=True)
    angefangen = []
    try:
        for f in daten:
            # nur="xml": erst die kleinen Beschreibungen (schnelle Liste)
            # nur="ton": später die WAVs im Hintergrund
            if nur == "xml" and not f.lower().endswith(".xml"):
                continue
            if nur == "ton" and f.lower().endswith(".xml"):
                continue
            if stopp is not None and stopp():
                raise g.Abgebrochen()
            ziel_datei = os.path.join(ziel, f)
            war_schon_da = os.path.exists(ziel_datei)
            if war_schon_da and not ueberschreiben:
                # schon vorhanden: nur ersetzen, wenn es im Pedal anders ist
                # (Größe oder Änderungszeit). Sonst bleibt die Datei stehen —
                # ein zweiter Lauf ist dann sehr schnell (16.11.2026).
                try:
                    fern = g.info(pfad.rstrip("/") + "/" + f)
                    gleich = (os.path.getsize(ziel_datei) == fern["groesse"])
                except Exception:
                    gleich = True
                if gleich and not f.lower().endswith(".xml"):
                    # unverändert und keine Beschreibung: nicht noch einmal
                    # holen. (Die Zuweisung an `ueberschreiben` stand hier
                    # früher fälschlich und ließ danach ALLE Dateien neu
                    # laden — am 16.11.2026 gefunden.)
                    if zaehler is not None:
                        zaehler["uebersprungen"] = zaehler.get("uebersprungen", 0) + 1
                    continue
                war_schon_da = False
            angefangen.append((ziel_datei, war_schon_da))
            # erst als .part schreiben, dann umbenennen: bricht der Vorgang ab,
            # bleibt keine halbe WAV zurück, die später wie eine echte aussieht
            inhalt = g.lies_datei(pfad.rstrip("/") + "/" + f, stopp=stopp)
            # Einmal wiederholen, wenn Ordner oder .part zwischenzeitlich
            # verschwunden sind (17.11.2026: „No such file … .part -> …wav“).
            for versuch in (1, 2):
                try:
                    os.makedirs(ziel, exist_ok=True)
                    with open(ziel_datei + ".part", "wb") as fp:
                        fp.write(inhalt)
                    os.replace(ziel_datei + ".part", ziel_datei)
                    break
                except FileNotFoundError:
                    if versuch == 2:
                        raise RuntimeError(
                            T("Die Datei konnte nicht geschrieben werden: %s")
                            % ziel_datei)
            if zaehler is not None:
                zaehler["gelesen"] = zaehler.get("gelesen", 0) + 1
            if protokoll is not None and not war_schon_da:
                protokoll.append(ziel_datei)
        for u in unter:
            if stopp is not None and stopp():
                raise g.Abgebrochen()
            _patch_holen(g, pfad.rstrip("/") + "/" + u, os.path.join(ziel, u),
                         stopp=stopp, nur=nur, protokoll=protokoll,
                         ueberschreiben=ueberschreiben, zaehler=zaehler)
    except g.Abgebrochen:
        for d, war_schon_da in angefangen:
            # nur entfernen, was dieser Lauf NEU angelegt hat — eine frühere
            # Sicherung im selben Ordner darf ein Abbruch nicht löschen
            for rest in (d, d + ".part"):
                try:
                    if rest.endswith(".part") or not war_schon_da:
                        if os.path.exists(rest):
                            os.remove(rest)
                except OSError:
                    pass
        raise
    return True


def _patch_schreiben(g, quell_ordner, ziel_pfad, meldung=None, stopp=None):
    """Einen Ordner vom Rechner ins Pedal schreiben (rekursiv).

    Schreibt Datei für Datei und meldet den Fortschritt. Vorher muß der
    Aufrufer sichern, was am Ziel schon liegt — diese Funktion überschreibt.
    """
    for eintrag in sorted(os.listdir(quell_ordner)):
        quelle = os.path.join(quell_ordner, eintrag)
        ziel = ziel_pfad.rstrip("/") + "/" + eintrag
        if stopp is not None and stopp():
            raise g.Abgebrochen()
        if os.path.isdir(quelle):
            g.erstelle_ordner(ziel)
            _patch_schreiben(g, quelle, ziel, meldung=meldung, stopp=stopp)
        else:
            with open(quelle, "rb") as fp:
                daten = fp.read()
            if meldung:
                meldung(eintrag, len(daten))
            g.schreibe_datei(ziel, daten, stopp=stopp)
    return True


def sicherung_begrenzen(platz_ordner, behalten=5):
    """Je Platz nur die letzten fünf Sicherungen behalten.

    Anlaß (17.11.2026, Wunsch des Nutzers): „vor-ueberschreiben" wuchs mit
    jedem Schreibvorgang. Jede Sicherung liegt jetzt in einem eigenen Ordner
    mit Zeitstempel; ältere werden hier entfernt — nur innerhalb dieses
    Ordners und nur die eigenen Einträge (Namen beginnen mit einer Ziffer).
    """
    import shutil

    try:
        eintraege = [d for d in os.listdir(platz_ordner)
                     if d[:1].isdigit()
                     and os.path.isdir(os.path.join(platz_ordner, d))]
    except OSError:
        return 0
    eintraege.sort()
    entfernt = 0
    for alt_name in (eintraege[:-behalten] if len(eintraege) > behalten else []):
        try:
            shutil.rmtree(os.path.join(platz_ordner, alt_name))
            entfernt += 1
        except OSError:
            pass
    return entfernt



def setlists_anpassen(ordner, zuege, stempel=None):
    """Setlist-Dateien im Ordner auf neue Platznummern umschreiben.

    Eine Setlist ist eine Textdatei mit „PatchNN<TAB>Name“ je Zeile (siehe
    _setlist_speichern). Nach dem Aufrücken zeigen die Nummern auf andere
    Patches; hier werden sie mitgezogen. Jede geänderte Datei wird vorher
    daneben gesichert. Rückgabe: [(Datei, Anzahl geänderter Zeilen)].
    """
    import re as _re

    zuordnung = {"Patch%02d" % alt: "Patch%02d" % neu for alt, neu in zuege}
    if not zuordnung:
        return []
    stempel = stempel or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    muster = _re.compile(r"^(Patch\d{1,3})(\s*\t.*)?$", _re.IGNORECASE)
    geaendert = []
    for name in sorted(os.listdir(ordner)):
        if not name.lower().endswith(".txt"):
            continue
        pfad = os.path.join(ordner, name)
        if not os.path.isfile(pfad):
            continue
        try:
            with open(pfad, encoding="utf-8") as fp:
                zeilen = fp.read().splitlines()
        except Exception:
            continue
        neu_zeilen, anzahl = [], 0
        for zeile in zeilen:
            treffer = muster.match(zeile)
            if treffer:
                alt = treffer.group(1)
                schluessel = ("Patch%02d" % int(alt[5:]))
                ziel = zuordnung.get(schluessel)
                if ziel and ziel != schluessel:
                    zeile = ziel + (treffer.group(2) or "")
                    anzahl += 1
            neu_zeilen.append(zeile)
        if not anzahl:
            continue
        sicherung = os.path.join(ordner, "%s.vor-aufruecken-%s.txt"
                                 % (os.path.splitext(name)[0], stempel))
        try:
            shutil.copy2(pfad, sicherung)
        except Exception:
            pass
        with open(pfad, "w", encoding="utf-8") as fp:
            fp.write("\n".join(neu_zeilen) + "\n")
        geaendert.append((name, anzahl))
    return geaendert


FIRMWARE_DATEIEN = [
    # Was die Firmware ausmacht (aus dem Speicherabbild vom 17.11.2026).
    # .filetable fehlt absichtlich: das ist der Verzeichnisindex des
    # Dateisystems und wird vom Pedal selbst gepflegt.
    "/mnt/nandfs/usr/IOMgr",
    "/mnt/nandfs/usr/JamManUI",
    "/mnt/nandfs/usr/loopengine",
    "/mnt/nandfs/usr/usbserial",
    "/mnt/nandfs/lib/devb-sd-at91sam9g35",
    "/mnt/nandfs/lib/devc-serusart",
    "/mnt/nandfs/lib/devc-serusb_g",
    "/mnt/nandfs/lib/devu-g15-at91sam_g.so",
    # JamManSoloXT.xml bleibt draußen: darin stehen Einstellungen des Nutzers
    # (CurrentPatch, PedalTempo, AutoRecord), keine Firmware. Am 18.11.2026
    # geklärt, nachdem die Datei gelesen wurde.
]

FIRMWARE_ORDNER = ["/mnt/nandfs/usr/attributes"]

# Einzelstücke je Gerät: unit_guid.attr ist die Kennung DIESES Pedals.
# Sie darf nicht auf ein anderes Gerät wandern (18.11.2026 in der geholten
# attributes-Liste gefunden).
FIRMWARE_NICHT_KOPIEREN = {"unit_guid.attr"}

FIRMWARE_REIHENFOLGE = [
    # Zuerst die Bibliotheken, dann die Programme, zuletzt der USB-Treiber:
    # bricht es ab, ist die Verbindung bis zum Schluß erhalten.
    "/mnt/nandfs/lib/devb-sd-at91sam9g35",
    "/mnt/nandfs/lib/devc-serusart",
    "/mnt/nandfs/lib/devu-g15-at91sam_g.so",
    "/mnt/nandfs/usr/IOMgr",
    "/mnt/nandfs/usr/loopengine",
    "/mnt/nandfs/usr/JamManUI",
    "/mnt/nandfs/lib/devc-serusb_g",
    "/mnt/nandfs/usr/usbserial",
]


def firmware_holen(g, ziel_ordner, melden=None, stopp=None):
    """Die Systemdateien eines Pedals auf den Rechner holen (nur lesend).

    Legt sie unter ziel_ordner mit ihrem Pfad an (usr/IOMgr usw.), damit
    firmware_schreiben() sie zurücklegen kann. Rückgabe: Liste (Pfad, Größe).
    """
    geholt = []
    for pfad in FIRMWARE_DATEIEN:
        if stopp is not None and stopp():
            raise g.Abgebrochen()
        if melden:
            melden(pfad)
        try:
            groesse = g.info(pfad).get("groesse", 0)
        except Exception:
            continue                      # Datei gibt es auf diesem Pedal nicht
        daten = g.lies_datei(pfad, stopp=stopp)
        if len(daten) != groesse:
            raise RuntimeError(T("%s kam unvollständig (%d von %d Bytes).")
                               % (pfad, len(daten), groesse))
        hiesig = os.path.join(ziel_ordner, pfad.lstrip("/").replace("/", os.sep))
        os.makedirs(os.path.dirname(hiesig), exist_ok=True)
        with open(hiesig, "wb") as fp:
            fp.write(daten)
        geholt.append((pfad, groesse))
    for ordner in FIRMWARE_ORDNER:
        try:
            dateien, _ = g.ordner(ordner.rstrip("/") + "/")
        except Exception:
            continue
        for name in dateien:
            pfad = ordner.rstrip("/") + "/" + name
            if stopp is not None and stopp():
                raise g.Abgebrochen()
            daten = g.lies_datei(pfad, stopp=stopp)
            hiesig = os.path.join(ziel_ordner, pfad.lstrip("/").replace("/", os.sep))
            os.makedirs(os.path.dirname(hiesig), exist_ok=True)
            with open(hiesig, "wb") as fp:
                fp.write(daten)
            geholt.append((pfad, len(daten)))
    return geholt


def _datei_ersetzen(g, pfad, daten, stopp=None):
    """Eine laufende Programmdatei ersetzen, ohne sie zu überschreiben.

    Anlaß (18.11.2026): „Das Pedal meldet einen Fehler: Resource busy" — QNX
    läßt eine Datei nicht überschreiben, solange sie läuft. Deshalb erst
    daneben schreiben (…neu), dann umbenennen; klappt das nicht, wird die
    laufende Datei nach …alt weggeschoben und die neue an ihren Platz gelegt.
    """
    neu_pfad = pfad + ".neu"
    try:
        g.loesche(neu_pfad)
    except Exception:
        pass
    g.schreibe_datei(neu_pfad, daten, stopp=stopp)
    try:
        g.benenne_um(neu_pfad, pfad, ueberschreiben=True)
        return "umbenannt"
    except Exception:
        pass
    try:
        g.benenne_um(pfad, pfad + ".alt", ueberschreiben=True)
    except Exception:
        pass
    g.benenne_um(neu_pfad, pfad, ueberschreiben=True)
    return "alt weggeschoben"


def firmware_schreiben(g, quell_ordner, melden=None, stopp=None):
    """Die Systemdateien aus einem geholten Ordner ins Pedal schreiben.

    Reihenfolge siehe FIRMWARE_REIHENFOLGE; nach jeder Datei wird die Größe
    verglichen. Rückgabe: Liste (Pfad, geschrieben, geprüft).
    """
    ergebnis = []
    pfade = list(FIRMWARE_REIHENFOLGE)
    attributes = os.path.join(quell_ordner, "mnt", "nandfs", "usr", "attributes")
    if os.path.isdir(attributes):
        pfade.append("/mnt/nandfs/usr/attributes/")
    for pfad in pfade:
        if stopp is not None and stopp():
            raise g.Abgebrochen()
        ist_ordner = pfad.endswith("/")
        hiesig = os.path.join(quell_ordner, pfad.lstrip("/").replace("/", os.sep))
        if ist_ordner:
            g.erstelle_ordner(pfad.rstrip("/"))
            for name in sorted(os.listdir(hiesig)):
                if name in FIRMWARE_NICHT_KOPIEREN:
                    continue
                ganz = pfad + name
                with open(os.path.join(hiesig, name), "rb") as fp:
                    daten = fp.read()
                if melden:
                    melden(ganz, len(daten))
                g.schreibe_datei(ganz, daten, stopp=stopp)
                ergebnis.append((ganz, len(daten), True))
            continue
        if not os.path.exists(hiesig):
            continue
        with open(hiesig, "rb") as fp:
            daten = fp.read()
        if melden:
            melden(pfad, len(daten))
        _datei_ersetzen(g, pfad, daten, stopp=stopp)
        try:
            danach = g.info(pfad).get("groesse", -1)
        except Exception:
            danach = -1
        ergebnis.append((pfad, len(daten), danach == len(daten)))
    return ergebnis


def jamlists_lesen(db_pfad):
    """JamLists aus der Bibliothek von JamManager XT lesen.

    Aufbau (aus dem Programmtext, 17.11.2026): JamManager.db.sqlite mit den
    Tabellen jamlists(id, color, name) und loops(…, jamlistid, "Loop #", …).
    Eine JamList besteht aus 200 Zeilen — eine je Platz des Pedals; nur die
    belegten tragen einen Namen.

    Rückgabe: Liste von dicts {id, name, platz: [(nummer, name, datei)]}
    """
    import sqlite3

    if not os.path.exists(db_pfad):
        raise RuntimeError(T("Die Datei %s fehlt.") % db_pfad)
    liste = []
    verbindung = sqlite3.connect(db_pfad)
    try:
        for kennung, name in verbindung.execute(
                "SELECT id, name FROM jamlists ORDER BY id"):
            plaetze = []
            for nummer, loop_name, datei in verbindung.execute(
                    'SELECT "Loop #", Name, filepath FROM loops '
                    'WHERE jamlistid=? AND Name IS NOT NULL ORDER BY "Loop #"',
                    (kennung,)):
                plaetze.append((nummer, loop_name or "", datei or ""))
            liste.append({"id": kennung, "name": name or "", "platz": plaetze})
    finally:
        verbindung.close()
    return liste


def jamlist_schreiben(db_pfad, name, eintraege, farbe=0):
    """Eine JamList in eine JamManager-Bibliothek eintragen.

    eintraege: [(platz, name, datei)] — Plätze 1…200. Fehlende Plätze bleiben
    leer, wie in der Vorlage des Herstellers. Rückgabe: Kennung der neuen Liste.
    """
    import sqlite3

    verbindung = sqlite3.connect(db_pfad)
    try:
        kennung = verbindung.execute(
            "INSERT INTO jamlists (color, name) VALUES (?, ?)",
            (int(farbe), name)) .lastrowid
        vorhanden = {int(platz): (loop_name, datei)
                     for platz, loop_name, datei in eintraege}
        for platz in range(1, 201):
            loop_name, datei = vorhanden.get(platz, (None, None))
            verbindung.execute(
                'INSERT INTO loops (jamlistid, isvalid, inlibrary, "Loop #", '
                'Name, Rating, filepath) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (kennung, "false", "false", platz, loop_name, 0, datei))
        verbindung.commit()
    finally:
        verbindung.close()
    return kennung


def speicherabbild(g, ziel_datei, wurzel="/", tiefe=8, hoechstens=5000,
                   melden=None, stopp=None,
                   ohne=("/proc", "/dev", "/mnt/sdfs")):
    """Verzeichnisbaum des Pedals als Textdatei festhalten (nur lesend).

    Zweck (17.11.2026): Zwei Pedale mit unterschiedlicher Firmware sollen
    verglichen werden, ohne etwas zu schreiben. Aufgenommen werden Pfad und
    Größe jeder Datei; das reicht, um Firmware-Dateien zu finden und zu sehen,
    was sich zwischen zwei Fassungen unterscheidet.

    Rückgabe: Anzahl der Einträge.
    """
    zeilen = []
    stapel = [(wurzel.rstrip("/") + "/", 0)]
    while stapel:
        pfad, tiefe_jetzt = stapel.pop(0)
        if stopp and stopp():
            raise RuntimeError(T("Abgebrochen."))
        if tiefe_jetzt > tiefe or len(zeilen) >= hoechstens:
            continue
        # /proc enthält Laufzeitspeicher (z. B. /proc/1/as), /dev die Geräte
        # und /mnt/sdfs die SD-Karte des Nutzers. Nichts davon gehört in einen
        # Firmware-Vergleich (18.11.2026: 222 Unterschiede, fast alle von der
        # eingelegten Karte).
        if any(pfad.startswith(anf) for anf in ohne):
            continue
        try:
            dateien, ordner = g.ordner(pfad)
        except Exception:
            continue
        for name in dateien:
            voll = pfad + name
            try:
                groesse = g.info(voll).get("groesse", 0)
            except Exception:
                groesse = 0
            zeilen.append("%s\t%d\tD" % (voll, groesse))
            if melden and len(zeilen) % 25 == 0:
                melden(len(zeilen), voll)
        for name in ordner:
            voll = pfad + name
            zeilen.append("%s\t0\tO" % voll)
            stapel.append((voll + "/", tiefe_jetzt + 1))
    with open(ziel_datei, "w", encoding="utf-8") as fp:
        fp.write("# JamMan Card Tool — " + T("Speicherabbild") + "\n")
        fp.write("# %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fp.write("# Pfad\tGroesse\tArt (D=Datei, O=Ordner)\n")
        fp.write("\n".join(zeilen) + "\n")
    return len(zeilen)


def speicherabbilder_vergleichen(datei_a, datei_b):
    """Zwei Speicherabbilder vergleichen. Rückgabe: Liste von Sätzen."""
    def lesen(pfad):
        werte = {}
        with open(pfad, encoding="utf-8") as fp:
            for zeile in fp:
                if zeile.startswith("#") or not zeile.strip():
                    continue
                teile = zeile.rstrip("\n").split("\t")
                if len(teile) >= 3:
                    werte[teile[0]] = (int(teile[1] or 0), teile[2])
        return werte

    a, b = lesen(datei_a), lesen(datei_b)
    nur_a = sorted(set(a) - set(b))
    nur_b = sorted(set(b) - set(a))
    anders = sorted(p for p in set(a) & set(b) if a[p][0] != b[p][0])
    return nur_a, nur_b, [(p, a[p][0], b[p][0]) for p in anders]


def plaetze_zusammenruecken(g, basis):
    """Belegte Plätze lückenlos nach vorn ziehen.

    Gibt [(alt, neu)] zurück — die Reihenfolge der Patches bleibt, nur die
    Plätze rücken auf. Wird erst nach einer Rückfrage ausgeführt, weil sich
    damit die Platznummern aller späteren Patches ändern.
    """
    import re as _re

    _, ordner = g.ordner(basis)
    muster = _re.compile(r"^Patch(\d{1,3})$", _re.IGNORECASE)
    belegt = sorted(int(muster.match(name).group(1)) for name in ordner
                    if muster.match(name))
    zuege = []
    for ziel, alt in enumerate(belegt, 1):
        if ziel != alt:
            zuege.append((alt, ziel))
    return zuege


def patches_aufs_pedal(g, basis, quell, ziel_ordner, melden=None, stopp=None):
    """Alle Patch-Ordner aus `quell` in den Speicher `basis` schreiben.

    Rückgabe: (geschrieben, übersprungen, gesichert).
    Belegte Plätze werden vorher nach `vor-ueberschreiben/<Platz>/<Stempel>`
    geholt (fünf Stände je Platz bleiben), unveränderte übersprungen.
    """
    import re as _re

    sag = melden or (lambda text: None)
    muster = _re.compile(r"^Patch(\d{1,3})$", _re.IGNORECASE)
    patches = []
    for name in sorted(os.listdir(quell)):
        treffer = muster.match(name)
        if treffer and os.path.isdir(os.path.join(quell, name)):
            patches.append((name, "Patch%02d" % int(treffer.group(1))))

    geschrieben = uebersprungen = gesichert = 0
    for i, (name, platz) in enumerate(patches, 1):
        if stopp is not None and stopp():
            raise g.Abgebrochen()
        sag("%s %d/%d — %s" % (T("Schreibe"), i, len(patches), platz))
        quell_patch = os.path.join(quell, name)
        ziel_pfad = basis + platz

        # unverändert? (gleiche Dateien, gleiche Größen im Pedal)
        try:
            _, vorhandene = g.ordner(ziel_pfad)
            gleich = bool(vorhandene)
            for wurzel, _, dateien in os.walk(quell_patch):
                for datei in dateien:
                    if not gleich:
                        break
                    hiesig = os.path.join(wurzel, datei)
                    fern = ziel_pfad + hiesig[len(quell_patch):].replace(os.sep, "/")
                    try:
                        if g.info(fern)["groesse"] != os.path.getsize(hiesig):
                            gleich = False
                    except Exception:
                        gleich = False
            if gleich:
                uebersprungen += 1
                continue
        except Exception:
            pass

        # belegt? vorher sichern und leeren
        try:
            _, vorhandene = g.ordner(ziel_pfad)
            if vorhandene:
                platz_ordner = os.path.join(ziel_ordner, "vor-ueberschreiben", platz)
                sicherung = os.path.join(
                    platz_ordner, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
                sag(T("Sichere den alten Platz …") + " " + platz)
                _patch_holen(g, ziel_pfad, sicherung)
                sicherung_begrenzen(platz_ordner)
                gesichert += 1
                g.loesche(ziel_pfad, ist_ordner=True, rekursiv=True)
        except Exception:
            pass

        g.erstelle_ordner(ziel_pfad)
        _patch_schreiben(g, quell_patch, ziel_pfad, stopp=stopp)
        geschrieben += 1
    return geschrieben, uebersprungen, gesichert


def _hol_wurzel(ziel, geraetename, speicher):
    """Zielordner für einen Hole-Lauf — je Lauf ein eigener Unterordner.

    Anlaß (16.11.2026): Alles landete in <Ziel>/<Gerät>. Lag dort schon eine
    frühere Kopie, wurden vorhandene Dateien übersprungen — und im
    Hauptfenster standen danach die alten Namen. Jetzt bekommt jeder Lauf
    seinen eigenen Ordner mit Datum und Speicher.
    """
    # Fester Ordner JE SPEICHER (nicht je Lauf): so werden beim zweiten Mal
    # nur die fehlenden oder geänderten Dateien geholt. Mit einem neuen
    # Ordner je Lauf wurde jedes Mal alles wieder geladen (16.11.2026).
    teil = "intern" if speicher == "intern" else "karte"
    return os.path.join(ziel, teil, geraetename)


def _als_zip(g, basis, platzliste, ziel_datei, meldung=None, stopp=None):
    """Alle Patches eines Speichers als ZIP sichern (ohne Umweg über den Ordner).

    Der Inhalt entspricht dem Ordner, den „Alle holen“ anlegt: <Gerät>/PatchNN/…
    """
    # Erst als .part schreiben und am Ende umbenennen (19.11.2026): Wird der
    # Vorgang abgebrochen, bleibt sonst eine halbe ZIP-Datei liegen, die wie
    # eine fertige Sicherung aussieht.
    zwischen = ziel_datei + ".part"
    geraetename = os.path.basename(basis.rstrip("/")) or "JamMan"
    geschafft = False
    try:
        _zip_schreiben(g, basis, platzliste, zwischen, geraetename,
                       meldung=meldung, stopp=stopp)
        os.replace(zwischen, ziel_datei)
        geschafft = True
        return True
    finally:
        if not geschafft and os.path.exists(zwischen):
            # Abbruch oder Fehler: keine halbe Sicherung liegen lassen.
            try:
                os.remove(zwischen)
            except OSError:
                pass


def _zip_schreiben(g, basis, platzliste, zwischen, geraetename, meldung=None,
                   stopp=None):
    import zipfile
    with zipfile.ZipFile(zwischen, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        def sammle(pfad, innen):
            dateien, ordner = g.ordner(pfad)
            for f in dateien:
                if stopp is not None and stopp():
                    raise g.Abgebrochen()
                if meldung:
                    meldung(f, 0)
                daten = g.lies_datei(pfad + "/" + f, stopp=stopp)
                # WAV lässt sich nicht sinnvoll komprimieren; das Packen
                # kostete bei großen Loops Minuten und ließ „Abbrechen“
                # wirkungslos erscheinen (16.11.2026).
                art = (zipfile.ZIP_STORED if f.lower().endswith(".wav")
                       else zipfile.ZIP_DEFLATED)
                z.writestr(geraetename + "/" + innen + f, daten, compress_type=art)
            for o in ordner:
                sammle(pfad.rstrip("/") + "/" + o, innen + o + "/")
        for i, platz in enumerate(platzliste, 1):
            if stopp is not None and stopp():
                raise g.Abgebrochen()
            if meldung:
                meldung(platz, i)
            sammle(basis + platz, platz + "/")


def frage_ja_nein_konsole(text=None):
    """Rückfrage in der Konsole (für das Schreiben von Firmware)."""
    if text is None:
        text = T("Firmware-Dateien jetzt ins Pedal schreiben? Ein Abbruch "
                 "mittendrin kann das Pedal unbrauchbar machen. [ja/nein] ")
    try:
        antwort = input("  " + text).strip().lower()
    except EOFError:
        return False
    return antwort in ("j", "ja", "y", "yes")


def _setlist_speichern(patchliste, indizes, ziel_datei, geraetename=""):
    """Eine Auswahl von Patches als Setlist (Textdatei) sichern."""
    with open(ziel_datei, "w", encoding="utf-8") as fp:
        fp.write("# " + T("JamMan Card Tool") + " — " + T("Karte und interner Speicher") + "\n")
        if geraetename:
            fp.write("# Gerät: %s\n" % geraetename)
        for i in indizes:
            platz, name = patchliste[i][0], patchliste[i][1]
            fp.write("%s\t%s\n" % (platz, name or ""))
    return True


def _setlist_lesen(pfad):
    """Setlist einlesen: [(Platz, Name)] — Zeilen mit # sind Bemerkungen."""
    eintraege = []
    with open(pfad, encoding="utf-8") as fp:
        for zeile in fp:
            zeile = zeile.rstrip("\n")
            if not zeile.strip() or zeile.lstrip().startswith("#"):
                continue
            teile = zeile.split("\t")
            eintraege.append((teile[0].strip(), teile[1] if len(teile) > 1 else ""))
    return eintraege


_USB_GERAET = {"obj": None}


def _geraet_holen(g_modul, anschuss=None):
    """Genau EIN Zugang zum Pedal je Programm.

    Zwei gleichzeitig geöffnete Anschlüsse (etwa nach „Verbindung neu
    aufbauen", während noch ein Strang las) brachten das Pedal am 16.11.2026
    zum Schweigen: es bekam Rahmen von zwei Sprechern und verlor die
    Ausrichtung. „Hole Beschreibungen …" blieb dann stehen.
    """
    if _USB_GERAET["obj"] is None:
        _USB_GERAET["obj"] = g_modul.JamManUSB(anschuss)
    return _USB_GERAET["obj"]


def _geraet_schliessen():
    try:
        if _USB_GERAET["obj"] is not None:
            _USB_GERAET["obj"].close()
    except Exception:
        pass
    _USB_GERAET["obj"] = None


_USB_FENSTER = {"fenster": None}


def teilen_fenster(datei, haupfenster, teile=2, schwelle=35.0, min_pause=0.25,
                   fade_naht=0.0, an_pausen=True):
    """Wellenform-Fenster zum Aufteilen einer Datei — der eine Ort dafür.

    Marken mit der Maus setzen (Klick) oder eintippen; beides gilt gleichzeitig.
    Ohne Marken teilt es in N Teile, an Pausen oder gleichmäßig.

    Rückgabe: {"marken", "teile", "pausen", "schwelle", "pause", "fade"}
    oder None, wenn abgebrochen wurde.
    """
    import tkinter as tk
    from tkinter import messagebox

    pruef = wav_info(datei)
    wav = datei
    zwischer = None
    if not (datei.lower().endswith(".wav") and pruef[1] == 44100 and pruef[3] == 16):
        zwischer = tempfile.mkdtemp(prefix="jamman-teilen-")
        ziel = os.path.join(zwischer, "ganz.wav")
        if not wandle_nach_44k(datei, ziel):
            shutil.rmtree(zwischer, ignore_errors=True)
            messagebox.showerror(T("Fehler"), T("Wellenform nicht lesbar."),
                                 parent=haupfenster)
            return None
        wav = ziel
    werte, dauer, _ = wellenform(wav, 900)
    if not werte or dauer <= 0:
        if zwischer:
            shutil.rmtree(zwischer, ignore_errors=True)
        messagebox.showerror(T("Fehler"), T("Wellenform nicht lesbar."),
                             parent=haupfenster)
        return None

    f = tk.Toplevel(haupfenster)
    f.title(T("Datei teilen"))
    f.configure(bg=FARBE_HINTERGRUND)
    f.geometry("960x390+%d+%d" % (haupfenster.winfo_rootx() + 40,
                                  haupfenster.winfo_rooty() + 40))
    try:
        f.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(f, text=os.path.basename(datei), bg=FARBE_HINTERGRUND,
             fg=FARBE_SCHRIFT, font=schrift(12, fest=True)).pack(pady=(10, 0))
    kopf = T("%s   •   Klick setzt eine Marke, Rechtsklick entfernt sie")
    tk.Label(f, text=kopf % sekunden(dauer), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE, font=schrift(9)).pack(pady=(0, 6))

    breite, hoehe = 900, 150
    leinwand = tk.Canvas(f, width=breite, height=hoehe, bg="#101820",
                         highlightthickness=0)
    leinwand.pack(padx=16)

    zust = {"marken": [], "welle": werte, "dauer": dauer,
            "breite": breite, "hoehe": hoehe}

    def zeit_zu_x(zeit):
        return zust["breite"] * (zeit / zust["dauer"])

    def x_zu_zeit(x):
        return max(0.0, min(zust["dauer"], zust["dauer"] * x / float(zust["breite"])))

    def grenzen():
        """Teilgrenzen für die Vorschau - ohne Marken nicht bestimmbar."""
        marken = sorted(zust["marken"])
        if marken:
            return [0.0, *marken, zust["dauer"]]
        return None

    def sperren(nach):
        """Ohne Marken gilt Teile/Pausen, mit Marken sind sie gegenstandslos."""
        zustand_feld = "disabled" if nach else "normal"
        for feld in (teile_eingabe,):
            feld.config(state=zustand_feld)
        pausen_haken.config(state=zustand_feld)

    def vorschau():
        teile_liste = grenzen()
        sperren(bool(teile_liste))
        if teile_liste:
            stuecke = ["%s–%s" % (sekunden(a), sekunden(b))
                       for a, b in zip(teile_liste, teile_liste[1:])]
            vorschau_var.set(T("%d Teile: %s") % (len(stuecke), "  ·  ".join(stuecke)))
            return
        try:
            an = int(teile_var.get().strip() or "2")
        except ValueError:
            vorschau_var.set(T("Bei Teile bitte eine ganze Zahl eintragen."))
            return
        if an < 2 or an > 60:
            vorschau_var.set(T("Bitte 2 bis 60 Teile angeben."))
            return
        if pausen_var.get():
            vorschau_var.set(T("%d Teile, Schnitt an Pausen gesucht") % an)
        else:
            vorschau_var.set(T("%d Teile, gleichmäßig geschnitten") % an)

    def zeichne():
        leinwand.delete("all")
        b, h = zust["breite"], zust["hoehe"]
        unten_leiste = 18
        hw = h - unten_leiste
        mitte = hw // 2
        schritt = b / float(max(1, len(zust["welle"])))
        for i, wert in enumerate(zust["welle"]):
            x = i * schritt
            k = max(1, int(wert * (hw / 2 - 6)))
            leinwand.create_line(x, mitte - k, x, mitte + k, fill="#5a9fd4")
        leinwand.create_line(0, mitte, b, mitte, fill="#333333")
        leinwand.create_rectangle(0, hw, b, h, fill="#0a1016", outline="")
        for k in range(9):
            x = b * k / 8.0
            leinwand.create_line(x, hw, x, hw + 4, fill="#556677")
            if k == 8:
                leinwand.create_text(b - 4, hw + 2, anchor="ne", fill="#c8d8e8",
                                     font=schrift(8), text=sekunden(zust["dauer"]))
            else:
                leinwand.create_text(x + 3, hw + 2, anchor="nw", fill="#c8d8e8",
                                     font=schrift(8),
                                     text=sekunden(zust["dauer"] * k / 8.0))
        for nr, zeit in enumerate(sorted(zust["marken"]), start=1):
            x = zeit_zu_x(zeit)
            leinwand.create_line(x, 0, x, hw, fill="#e05a5a", width=2)
            leinwand.create_text(x + 3, 8, anchor="nw", fill="#ffd27f",
                                 text="%d" % nr, font=schrift(9, fest=True))
        marken_var.set(", ".join("%.2f" % m for m in sorted(zust["marken"])))
        vorschau()

    def klick(e):
        zust["marken"].append(x_zu_zeit(e.x))
        zeichne()

    def rechtsklick(e):
        if not zust["marken"]:
            return
        zeit = x_zu_zeit(e.x)
        naechste = min(zust["marken"], key=lambda m: abs(m - zeit))
        if abs(zeit_zu_x(naechste) - e.x) <= 12:
            zust["marken"].remove(naechste)
            zeichne()

    leinwand.bind("<Button-1>", klick)
    leinwand.bind("<Button-2>", rechtsklick)      # macOS: rechte Taste
    leinwand.bind("<Button-3>", rechtsklick)

    mitte_rahmen = tk.Frame(f, bg=FARBE_HINTERGRUND)
    mitte_rahmen.pack(fill="x", padx=16, pady=(8, 0))
    tk.Label(mitte_rahmen, text=T("Marken"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    marken_var = tk.StringVar()
    marken_feld = tk.Entry(mitte_rahmen, textvariable=marken_var, width=30,
                           bg="#ffffff", fg="#000000", insertbackground="#000000",
                           relief="solid", borderwidth=1)
    marken_feld.pack(side="left", padx=(6, 10))

    def marken_aus_feld(_e=None):
        roh = marken_var.get().replace(";", ",")
        neu = []
        for stueck in roh.split(","):
            stueck = stueck.strip().replace(",", ".")
            if not stueck:
                continue
            try:
                neu.append(float(stueck))
            except ValueError:
                status_var.set(T("Marken bitte als Zahlen mit Komma, z. B. 60, 120."))
                return
        drin = [m for m in neu if 0.0 < m < zust["dauer"]]
        if len(drin) != len(neu):
            status_var.set(T("Marken außerhalb der Datei wurden verworfen."))
        else:
            status_var.set("")
        zust["marken"] = drin
        zeichne()

    marken_feld.bind("<Return>", marken_aus_feld)
    marken_feld.bind("<FocusOut>", marken_aus_feld)

    def alle_weg():
        zust["marken"] = []
        status_var.set("")
        zeichne()

    def pausen_suchen():
        try:
            sch = float(schwelle_var.get().replace(",", ".") or "35")
            pa = float(pause_var.get().replace(",", ".") or "0.25")
        except ValueError:
            status_var.set(T("Schwelle, Pause und Fade bitte als Zahlen eintragen."))
            return
        punkte, _, _ = finde_schnittpunkte(wav, 0, min_pause=pa,
                                           schwelle=sch / 100.0,
                                           alle_pausen=True)
        zust["marken"] = list(punkte)
        if punkte:
            status_var.set(T("Pausen gefunden: %d") % len(punkte))
        else:
            status_var.set(T("Keine Pause gefunden."))
        zeichne()

    tk.Button(mitte_rahmen, text=T("Alle Marken löschen"), command=alle_weg,
              relief="solid", borderwidth=1).pack(side="left", padx=(0, 6))
    tk.Button(mitte_rahmen, text=T("Alle Pausen als Marken"), command=pausen_suchen,
              relief="solid", borderwidth=1).pack(side="left")

    unten = tk.Frame(f, bg=FARBE_HINTERGRUND)
    unten.pack(fill="x", padx=16, pady=(8, 0))
    pausen_var = tk.BooleanVar(value=bool(an_pausen))
    pausen_haken = tk.Checkbutton(unten, text=T("an Pausen schneiden"),
                                  variable=pausen_var,
                                  bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                                  activebackground=FARBE_HINTERGRUND,
                                  activeforeground=FARBE_SCHRIFT,
                                  selectcolor=FARBE_HINTERGRUND,
                                  command=vorschau)
    pausen_haken.pack(side="left", padx=(0, 12))
    tk.Label(unten, text=T("Teile"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    teile_var = tk.StringVar(value=str(teile))
    teile_eingabe = tk.Entry(unten, textvariable=teile_var, width=4, justify="center",
                             bg="#ffffff", fg="#000000", insertbackground="#000000",
                             relief="solid", borderwidth=1)
    teile_eingabe.pack(side="left", padx=(4, 10))
    teile_eingabe.bind("<KeyRelease>", lambda e: vorschau())
    tk.Label(unten, text=T("Schwelle %"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    schwelle_var = tk.StringVar(value=("%g" % schwelle))
    tk.Entry(unten, textvariable=schwelle_var, width=4, justify="center",
             bg="#ffffff", fg="#000000", insertbackground="#000000",
             relief="solid", borderwidth=1).pack(side="left", padx=(4, 10))
    tk.Label(unten, text=T("Pause s"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    pause_var = tk.StringVar(value=("%g" % min_pause))
    tk.Entry(unten, textvariable=pause_var, width=5, justify="center",
             bg="#ffffff", fg="#000000", insertbackground="#000000",
             relief="solid", borderwidth=1).pack(side="left", padx=(4, 10))
    tk.Label(unten, text=T("Fade ms"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    fade_var = tk.StringVar(value=("%g" % fade_naht))
    tk.Entry(unten, textvariable=fade_var, width=4, justify="center",
             bg="#ffffff", fg="#000000", insertbackground="#000000",
             relief="solid", borderwidth=1).pack(side="left")

    vorschau_var = tk.StringVar(value="")
    tk.Label(f, textvariable=vorschau_var, bg=FARBE_HINTERGRUND, fg=FARBE_TON,
             font=schrift(9), wraplength=900, justify="left").pack(pady=(8, 0))

    status_var = tk.StringVar(value="")
    tk.Label(f, textvariable=status_var, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9)).pack(pady=(2, 0))

    ergebnis = {"wert": None}

    def fertig():
        try:
            sch = float(schwelle_var.get().replace(",", ".") or "35")
            pa = float(pause_var.get().replace(",", ".") or "0.25")
            fa = float(fade_var.get().replace(",", ".") or "0")
            an = int(teile_var.get().strip() or "2")
        except ValueError:
            status_var.set(T("Schwelle, Pause und Fade bitte als Zahlen eintragen."))
            return
        if not (1.0 <= sch <= 99.0):
            status_var.set(T("Schwelle bitte als Zahl zwischen 1 und 99."))
            return
        if not (0.01 <= pa <= 30.0):
            status_var.set(T("Pause bitte als Zahl zwischen 0,01 und 30 Sekunden."))
            return
        if not (0.0 <= fa <= 50.0):
            status_var.set(T("Fade bitte als Zahl zwischen 0 und 50."))
            return
        marken_aus_feld()
        if not zust["marken"]:
            if not (2 <= an <= 60):
                status_var.set(T("Bitte 2 bis 60 Teile angeben."))
                return
        ergebnis["wert"] = {"marken": sorted(zust["marken"]), "teile": an,
                            "schwelle": sch, "pause": pa, "fade": fa,
                            "pausen": bool(pausen_var.get())}
        f.destroy()

    knoepfe = tk.Frame(f, bg=FARBE_HINTERGRUND)
    knoepfe.pack(pady=(10, 12))
    tk.Button(knoepfe, text=T("Abbrechen"), command=f.destroy, relief="solid",
              borderwidth=1, width=14).pack(side="left", padx=(0, 8))
    tk.Button(knoepfe, text=T("Teilen"), command=fertig, relief="solid",
              borderwidth=1, width=14).pack(side="left")

    zeichne()
    f.wait_window()
    if zwischer:
        shutil.rmtree(zwischer, ignore_errors=True)
    return ergebnis["wert"]

def usb_fenster(haupfenster, oeffne_karte=None):
    """Fenster „Pedal über USB": Speicher wählen, Patches ansehen, sichern.

    Regeln, die aus dem ersten Versuch am 16.11.2026 stammen:
      - nur EIN Fenster. Ein zweiter Klick holt das vorhandene nach vorn,
        statt ein weiteres zu öffnen (es gingen mehrere auf, das Programm
        wurde träge).
      - tkinter wird ausschließlich im Hauptstrang benutzt. Der Arbeitsstrang
        legt seine Ergebnisse in eine Warteschlange, die Oberfläche liest sie
        mit after() ab. Ein Tk-Aufruf aus einem anderen Strang kann das
        Fenster blockieren.
      - der gefundene Anschluß wird gemerkt, damit beim nächsten Mal nicht
        wieder alle Anschlüsse durchprobiert werden (das kostete Sekunden).
    """
    from tkinter import messagebox

    offen = _USB_FENSTER.get("fenster")
    if offen is not None:
        try:
            offen.deiconify()
            offen.lift()
            offen.focus_force()
            return
        except Exception:
            _USB_FENSTER["fenster"] = None

    try:
        g_modul = _greenglass()
    except Exception as e:
        messagebox.showerror(T("Pedal über USB"),
                             T("Das USB-Modul fehlt: greenglass.py muß neben jamman.py liegen.")
                             + "\n" + str(e))
        return
    # Wenn beim Aufbau etwas schiefgeht, soll es zu sehen sein und nicht
    # nur im Terminal stehen (das Fenster blieb am 16.11.2026 unsichtbar).
    try:
        return _usb_fenster_bauen(haupfenster, g_modul, oeffne_karte)
    except Exception as e:
        import traceback
        messagebox.showerror(T("Pedal über USB"),
                             T("Fehler beim Öffnen:") + "\n" + str(e) + "\n\n"
                             + traceback.format_exc()[-600:])
        return

def _neuer_patch_xml(geraetename, name):
    """patch.xml für einen neuen Patch bauen (Aufbau wie vom Pedal erzeugt)."""
    import uuid
    name = (name or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return ('<?xml version="1.0" encoding="UTF-8" ?>\n'
            '<JamManPatch xmlns="http://schemas.digitech.com/JamMan/Patch" '
            'device="%s" version="1">\n'
            '    <PatchName>%s</PatchName>\n'
            '    <RhythmType>Silence</RhythmType>\n'
            '    <StopMode>StopInstantly</StopMode>\n'
            '    <SettingsVersion>0</SettingsVersion>\n'
            '    <ID>%s</ID>\n'
            '    <Metadata>\n'
            '    <Author />\n'
            '    <Description />\n'
            '    <Artist />\n'
            '    <Genre>Unspecified</Genre>\n'
            '    <Rating>0</Rating>\n'
            '    <Tags />\n'
            '</Metadata>\n'
            '</JamManPatch>\n' % (geraetename, name, uuid.uuid4())).encode("utf-8")


def _neue_phrase_xml():
    """phrase.xml für eine neue Phrase (wie vom Pedal erzeugt)."""
    import uuid
    return ('<?xml version="1.0" encoding="UTF-8" ?>\n'
            '<JamManPhrase xmlns="http://schemas.digitech.com/JamMan/Phrase" version="1">\n'
            '    <BeatsPerMinute>90.0000000000</BeatsPerMinute>\n'
            '    <BeatsPerMeasure>4</BeatsPerMeasure>\n'
            '    <BpmValidated>0</BpmValidated>\n'
            '    <IsLoop>1</IsLoop>\n'
            '    <IsReversed>0</IsReversed>\n'
            '    <SettingsVersion>0</SettingsVersion>\n'
            '    <AudioVersion>0</AudioVersion>\n'
            '    <ID>%s</ID>\n'
            '    <Metadata />\n'
            '</JamManPhrase>\n' % (uuid.uuid4())).encode("utf-8")


# Mögliche Werte der Pedal-Einstellungen. Sie stammen aus dem Protokoll-
# dokument von RenanDiaz/jamman-manager (docs/usb-protocol.md) und von dem,
# was das Pedal selbst zurückmeldet. Unbekannte Werte werden zusätzlich
# angeboten, damit nichts verlorengeht.
WERTE_WARTESCHLANGE = ["RevertToSaved", "TransitionIfStopped"]
WERTE_TASTE = ["UndoRedo", "Reverse", "Tempo", ""]


def _frage_ja(fenster, titel, text):
    """Ja/Nein-Frage, die nicht hinter dem Vordergrundfenster verschwindet.

    Anlaß (16.11.2026): Das USB-Fenster steht dauerhaft im Vordergrund. Eine
    Rückfrage, die dahinter aufging, blieb unsichtbar — das Werkzeug wirkte
    eingefroren, obwohl es nur auf ein Ja oder Nein wartete. Vor der Frage
    wird der Vordergrund kurz abgegeben, danach wieder gesetzt.
    """
    vorne = False
    try:
        vorne = bool(fenster.attributes("-topmost"))
        if vorne:
            fenster.attributes("-topmost", False)
    except Exception:
        vorne = False
    try:
        return frage_ja_nein(fenster, titel, text)
    finally:
        try:
            if vorne:
                fenster.attributes("-topmost", True)
                fenster.lift()
        except Exception:
            pass


def _sicherungs_laeufe(ziel):
    """Alle Hole- und Sicherungsläufe unter einem Zielordner.

    Rückgabe: [(Pfad, Name, Änderungszeit, Größe in Bytes)] — nur direkte
    Unterordner, nie der Zielordner selbst.
    """
    laeufe = []
    if not ziel or not os.path.isdir(ziel):
        return laeufe
    # Nur was nach einer Sicherung aussieht. Anlaß (19.11.2026): Landete eine
    # Sicherung versehentlich im Benutzerverzeichnis, zeigte der Aufräumdialog
    # dort "Applications", "Documents" und so weiter — und hätte sie auf
    # Wunsch gelöscht.
    erkennbar = ("JamManSicherung_", "Sicherung_", "Hole_", "vor-ueberschreiben")
    for name in sorted(os.listdir(ziel)):
        pfad = os.path.join(ziel, name)
        if not os.path.isdir(pfad):
            continue
        if not name.startswith(erkennbar):
            continue
        try:
            laeufe.append((pfad, name, os.path.getmtime(pfad), _ordnergroesse(pfad)))
        except OSError:
            pass
    laeufe.sort(key=lambda x: x[2], reverse=True)   # neueste zuerst
    return laeufe


def unersetzliche_patches(laeufe):
    """Wie viele Patches je Sicherung gibt es NUR in dieser Sicherung?

    Anlaß (19.11.2026): „Gibt es eine Prüfung, welche Sicherung am wichtigsten
    ist?" Die gibt es nicht — aber diese hier ist ehrlich meßbar: jeder Patch
    trägt in seiner patch.xml eine Kennung (<ID>). Kommt eine Kennung in genau
    einer Sicherung vor, ist sie anderswo nicht mehr zu holen.

    Rückgabe: {Name der Sicherung: Anzahl einzigartiger Patches}. Gemessen an
    53 Sicherungen: 0,1 Sekunden, weil nur die kleinen patch.xml gelesen werden.
    """
    import collections
    import re as _re

    kennungen = {}
    for pfad, name, _wann, _groesse in laeufe:
        gefunden = set()
        for wurzel, _ordner, dateien in os.walk(pfad):
            if "patch.xml" not in dateien:
                continue
            try:
                with open(os.path.join(wurzel, "patch.xml"), encoding="utf-8",
                          errors="replace") as f:
                    text = f.read(4000)
            except OSError:
                continue
            treffer = _re.search(r"<ID>([^<]+)</ID>", text)
            if treffer:
                gefunden.add(treffer.group(1).strip())
        kennungen[name] = gefunden

    zaehler = collections.Counter()
    for menge in kennungen.values():
        zaehler.update(menge)
    return dict((name, sum(1 for k in menge if zaehler[k] == 1))
                for name, menge in kennungen.items())


def sammelsicherung(ziel, laeufe, melden=None):
    """Patches, die es nur in einer Sicherung gibt, in EINE Sicherung retten.

    Anlaß (19.11.2026): 53 Sicherungen mit 20,9 GB, davon 13 mit Patches, die
    es sonst nirgends gibt (797 MB). Statt alles zusammenzulegen — das wären
    Gigabyte — werden nur die unersetzlichen Patches in einen neuen Ordner
    kopiert. Danach sind die alten Sicherungen entbehrlich.

    Rückgabe: (Pfad der neuen Sicherung, Anzahl geretteter Patches).
    """
    import re as _re
    import shutil
    sag = melden or (lambda text: None)

    bestand = {}
    for pfad, _name, _wann, _groesse in laeufe:
        for wurzel, _ordner, dateien in os.walk(pfad):
            if "patch.xml" not in dateien:
                continue
            try:
                with open(os.path.join(wurzel, "patch.xml"), encoding="utf-8",
                          errors="replace") as f:
                    text = f.read(4000)
            except OSError:
                continue
            treffer = _re.search(r"<ID>([^<]+)</ID>", text)
            if treffer:
                bestand.setdefault(treffer.group(1).strip(), []).append((pfad, wurzel))
    einmalig = [quellen[0] for kennung, quellen in bestand.items() if len(quellen) == 1]
    if not einmalig:
        return "", 0

    stempel = datetime.now().strftime("%Y-%m-%d_%H-%M")
    zielordner = freier_name(os.path.join(ziel, "JamManSicherung_%s_rettung" % stempel))
    inhalt = os.path.join(zielordner, UNTERORDNER)
    os.makedirs(inhalt, exist_ok=True)

    zuordnung = ["Sammelsicherung vom %s" % datetime.now().strftime("%d.%m.%Y %H:%M"), ""]
    anzahl = 0
    for nummer, (quelldatei, patchpfad) in enumerate(einmalig, 1):
        neu = "Patch%02d" % nummer
        sag(((nummer - 1) / float(len(einmalig)),
             T("%d von %d Patches: %s") % (nummer, len(einmalig),
                                           os.path.basename(patchpfad))))
        shutil.copytree(patchpfad, os.path.join(inhalt, neu), dirs_exist_ok=True)
        zuordnung.append("%s\t%s\t%s" % (neu, os.path.basename(patchpfad),
                                          os.path.basename(quelldatei)))
        anzahl += 1
    try:
        with open(os.path.join(zielordner, "Rettung.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(zuordnung) + "\n")
    except OSError:
        pass
    sag(1.0, T("fertig"))
    return zielordner, anzahl


def setlistdateien_finden(ordner):
    """Setlist-Dateien in einem Ordner: [(Pfad, Name, Anzahl Plätze)].

    Eine Setlist ist eine Textdatei mit „PatchNN<TAB>Name" je Zeile. Gezählt
    wird, wie viele Platz-Zeilen darin stehen — das ist die brauchbarste
    Auskunft in einer Liste (19.11.2026).
    """
    import re as _re
    muster = _re.compile(r"^Patch\d{1,3}(\s*\t.*)?$", _re.IGNORECASE)
    treffer = []
    try:
        namen = sorted(os.listdir(ordner))
    except OSError:
        return treffer
    for name in namen:
        if not name.lower().endswith(".txt"):
            continue
        pfad = os.path.join(ordner, name)
        if not os.path.isfile(pfad):
            continue
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                anzahl = sum(1 for zeile in f if muster.match(zeile.strip()))
        except OSError:
            anzahl = 0
        if anzahl:
            treffer.append((pfad, name, anzahl))
    return treffer


def fenster_setlists(zuege, haupfenster):
    """Setlist-Dateien auflisten und die zu ändernden markieren.

    Rückgabe: (Ordner, Anzahl geänderter Dateien) — oder ("", 0).
    """
    import tkinter as tk
    from tkinter import messagebox

    ordner = einstellung_lesen("usb_setlist_pfad") or os.path.expanduser("~")
    while True:
        dateien = setlistdateien_finden(ordner)
        if not dateien:
            messagebox.showinfo(
                T("Setlists anpassen"),
                T("In diesem Ordner liegen keine Setlist-Dateien:\n%s\n\n"
                  "Bitte einen anderen Ordner wählen.") % ordner)
            neu = ordner_waehlen(haupfenster, T("Ordner mit Setlist-Dateien"),
                                 start=ordner)
            if not neu or os.path.abspath(neu) == os.path.abspath(ordner):
                return "", 0
            ordner = neu
            einstellung_schreiben("usb_setlist_pfad", ordner)
            continue
        break

    f = tk.Toplevel(haupfenster)
    f.title(T("Setlists anpassen"))
    f.configure(bg=FARBE_HINTERGRUND)
    f.geometry("620x430+%d+%d" % (haupfenster.winfo_rootx() + 60,
                                  haupfenster.winfo_rooty() + 60))
    try:
        f.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(f, text=T("Setlist-Dateien"), bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT,
             font=schrift(12, fest=True)).pack(pady=(12, 2))
    tk.Label(f, text=ordner, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9), wraplength=560).pack(pady=(0, 8))

    rahmen = tk.Frame(f, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=16)
    anzeige = tk.Listbox(rahmen, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                         selectbackground=FARBE_AUSWAHL, selectmode="extended",
                         exportselection=False, font=schrift(10))
    anzeige.pack(side="left", fill="both", expand=True)
    roll = tk.Scrollbar(rahmen, command=anzeige.yview)
    roll.pack(side="right", fill="y")
    anzeige.config(yscrollcommand=roll.set)
    for _pfad, name, anzahl in dateien:
        anzeige.insert("end", "%-40s %3d Plätze" % (name[:40], anzahl))
    for i in range(len(dateien)):
        anzeige.selection_set(i)

    tk.Label(f, text=T("Alle sind markiert — einzelne mit Klick abwählen."),
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9)).pack(pady=(6, 0))

    zustand = {"ordner": "", "anzahl": 0}

    def anpassen():
        gewaehlt = [dateien[i][0] for i in anzeige.curselection()]
        if not gewaehlt:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Datei markieren."),
                                parent=f)
            return
        geaendert = 0
        gesamt = 0
        for datei in gewaehlt:
            ergebnis = setlists_anpassen_eine(datei, zuege)
            if ergebnis:
                geaendert += 1
                gesamt += ergebnis
        zustand["ordner"] = ordner
        zustand["anzahl"] = geaendert
        messagebox.showinfo(
            T("Setlists anpassen"),
            T("%d Datei(en) angepasst, %d Zeile(n) umgeschrieben.")
            % (geaendert, gesamt), parent=f)
        f.destroy()

    def anderer_ordner():
        neu = ordner_waehlen(f, T("Ordner mit Setlist-Dateien"), start=ordner)
        if neu:
            einstellung_schreiben("usb_setlist_pfad", neu)
            zustand["ordner"] = neu
            f.destroy()
            zustand["neu"] = True

    knopfreihe = tk.Frame(f, bg=FARBE_HINTERGRUND)
    knopfreihe.pack(fill="x", padx=16, pady=(10, 12))
    tk.Button(knopfreihe, text=T("Anpassen"), command=anpassen).pack(side="right")
    tk.Button(knopfreihe, text=T("Abbrechen"),
              command=f.destroy).pack(side="right", padx=(0, 8))
    tk.Button(knopfreihe, text=T("Anderer Ordner …"),
              command=anderer_ordner).pack(side="left")
    anzeige.bind("<Return>", lambda e: anpassen())
    f.bind("<Escape>", lambda e: f.destroy())
    f.wait_window()
    if zustand.get("neu"):
        return fenster_setlists(zuege, haupfenster)
    return zustand["ordner"], zustand["anzahl"]


def setlists_anpassen_eine(pfad, zuege, stempel=None):
    """Setlist-Datei umschreiben, vorher sichern. Rückgabe: geänderte Zeilen."""
    import re as _re

    zuordnung = {"Patch%02d" % alt: "Patch%02d" % neu for alt, neu in zuege}
    muster = _re.compile(r"^(Patch\d{1,3})(\s*\t.*)?$", _re.IGNORECASE)
    try:
        with open(pfad, encoding="utf-8") as fp:
            zeilen = fp.read().splitlines()
    except OSError:
        return 0
    neu_zeilen, anzahl = [], 0
    for zeile in zeilen:
        treffer = muster.match(zeile)
        if treffer:
            alt = treffer.group(1)
            schluessel = "Patch%02d" % int(alt[5:])
            if schluessel in zuordnung:
                neu_zeilen.append(zuordnung[schluessel] + (treffer.group(2) or ""))
                anzahl += 1
                continue
        neu_zeilen.append(zeile)
    if not anzahl:
        return 0
    stempel = stempel or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        import shutil as _shutil
        _shutil.copy2(pfad, pfad + ".gesichert-" + stempel)
        with open(pfad, "w", encoding="utf-8") as fp:
            fp.write("\n".join(neu_zeilen) + "\n")
    except OSError:
        return 0
    return anzahl


def pegelprotokolle_auflisten(ordner):
    """Alle Pegelprotokolle im Sicherungsordner, neueste zuerst.

    Rückgabe: [(Pfad, Anlaß, Zeitpunkt, Anzahl Einträge)]. Der Name enthält
    Datum und Anlaß: Pegelprotokoll_2026-11-19_04-08-34_alle_normieren.txt
    """
    import re as _re
    import time as _zeit
    treffer = []
    try:
        namen = os.listdir(ordner)
    except OSError:
        return treffer
    muster = _re.compile(r"^Pegelprotokoll_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_(.+)\.txt$")
    for name in namen:
        t = muster.match(name)
        if not t:
            continue
        pfad = os.path.join(ordner, name)
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                anzahl = sum(1 for zeile in f
                             if zeile.strip() and not zeile.startswith("#") and "\t" in zeile)
        except OSError:
            anzahl = 0
        try:
            wann = _zeit.mktime(_zeit.strptime("%s %s" % (t.group(1), t.group(2)),
                                               "%Y-%m-%d %H-%M-%S"))
        except ValueError:
            wann = 0
        treffer.append((pfad, t.group(3).replace("_", " "), wann, anzahl))
    treffer.sort(key=lambda x: x[2], reverse=True)
    return treffer


def fenster_pegelprotokolle(ordner, haupfenster):
    """Protokolle auflisten und eines auswählen. Rückgabe: Pfad oder ""."""
    import time as _zeit
    import tkinter as tk
    from tkinter import messagebox

    protokolle = pegelprotokolle_auflisten(ordner)
    if not protokolle:
        messagebox.showinfo(T("Pegeländerung zurücknehmen"),
                            T("Es gibt noch keine Pegelprotokolle."))
        return ""

    f = tk.Toplevel(haupfenster)
    f.title(T("Pegeländerung zurücknehmen"))
    f.configure(bg=FARBE_HINTERGRUND)
    f.geometry("660x460+%d+%d" % (haupfenster.winfo_rootx() + 60,
                                  haupfenster.winfo_rooty() + 60))
    try:
        f.attributes("-topmost", True)
    except Exception:
        pass

    tk.Label(f, text=T("Pegelprotokolle"), bg=FARBE_HINTERGRUND,
             fg=FARBE_SCHRIFT, font=schrift(12, fest=True)).pack(pady=(12, 2))
    tk.Label(f, text=T("Welche Änderung soll zurückgenommen werden?"),
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(10)).pack(pady=(0, 8))

    rahmen = tk.Frame(f, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=16)
    anzeige = tk.Listbox(rahmen, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                         selectbackground=FARBE_AUSWAHL, exportselection=False,
                         font=schrift(10))
    anzeige.pack(side="left", fill="both", expand=True)
    roll = tk.Scrollbar(rahmen, command=anzeige.yview)
    roll.pack(side="right", fill="y")
    anzeige.config(yscrollcommand=roll.set)

    for i, (_pfad, anlass, wann, anzahl) in enumerate(protokolle):
        marke = "  ← neueste" if i == 0 else ""
        anzeige.insert("end", "%s   %-22s %3d Patches%s" % (
            _zeit.strftime("%d.%m.%Y %H:%M", _zeit.localtime(wann)),
            anlass[:22], anzahl, marke))
    anzeige.selection_set(0)

    # Zeile darunter: welche Patches würde die markierte Zeile treffen?
    # (Wunsch nach größtem Bedienkomfort, 19.11.2026.)
    vorschau = tk.StringVar(value="")
    def vorschau_zeigen(*_):
        gewaehlt = anzeige.curselection()
        if not gewaehlt:
            vorschau.set("")
            return
        pfad = protokolle[gewaehlt[0]][0]
        try:
            eintraege = lies_pegelprotokoll(pfad)
        except Exception:
            eintraege = []
        namen = [e["ordner"].replace("Patch", "") for e in eintraege]
        if len(namen) <= 8:
            text = ", ".join(namen)
        else:
            text = ", ".join(namen[:8]) + " …"
        vorschau.set(T("betrifft Platz: %s") % text if namen else "")
    anzeige.bind("<<ListboxSelect>>", vorschau_zeigen)
    vorschau_zeigen()
    tk.Label(f, textvariable=vorschau, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9), wraplength=600).pack(pady=(6, 0))
    anzeige.focus_set()
    f.bind("<Escape>", lambda e: f.destroy())

    wahl = {"pfad": ""}

    def uebernehmen(*_):
        gewaehlt = anzeige.curselection()
        if not gewaehlt:
            messagebox.showinfo(T("Hinweis"),
                                T("Erst ein Protokoll markieren."), parent=f)
            return
        wahl["pfad"] = protokolle[gewaehlt[0]][0]
        f.destroy()

    knopfreihe = tk.Frame(f, bg=FARBE_HINTERGRUND)
    knopfreihe.pack(fill="x", padx=16, pady=(8, 12))
    tk.Button(knopfreihe, text=T("Zurücknehmen"), command=uebernehmen).pack(side="right")
    tk.Button(knopfreihe, text=T("Abbrechen"),
              command=f.destroy).pack(side="right", padx=(0, 8))
    anzeige.bind("<Double-Button-1>", uebernehmen)
    anzeige.bind("<Return>", uebernehmen)
    f.wait_window()
    return wahl["pfad"]


def fenster_aufraeumen(ziel, haupfenster, sichern=None, karte=None):
    """Sicherungen im Zielordner ansehen und alte löschen.

    Anlaß (16.11.2026): Mit jedem Hole-Lauf wächst der Zielordner; nach
    mehreren Läufen lagen hunderte Megabyte alter Kopien herum.
    Gelöscht wird nur, was ausdrücklich angekreuzt ist — und nur innerhalb
    des Zielordners.
    """
    import shutil
    import tkinter as tk
    from tkinter import messagebox

    if not ziel or not os.path.isdir(ziel):
        messagebox.showinfo(T("Aufräumen"),
                            T("Der Zielordner ist noch nicht angelegt."))
        return

    laeufe = _sicherungs_laeufe(ziel)
    if not laeufe:
        messagebox.showinfo(T("Aufräumen"), T("Im Zielordner liegen keine Sicherungen."))
        return

    f = tk.Toplevel(haupfenster)
    f.title(T("Sicherungen aufräumen"))
    f.configure(bg=FARBE_HINTERGRUND)
    f.geometry("720x520+%d+%d" % (haupfenster.winfo_rootx() + 50,
                                  haupfenster.winfo_rooty() + 50))
    try:
        f.attributes("-topmost", True)
    except Exception:
        pass

    # Herausgeben: die Karte in etwas verwandeln, das ohne dieses Werkzeug
    # lesbar bleibt (20.11.2026).
    def herausgeben_jetzt():
        import tkinter.filedialog as fdialog
        basis = karte
        if not basis:
            messagebox.showinfo(T("Herausgeben"),
                                T("Erst eine Karte öffnen — dann läßt sie sich "
                                  "herausgeben."))
            return
        ziel_ordner = fdialog.askdirectory(parent=haupfenster,
                                            title=T("Wohin herausgeben?"),
                                            mustexist=False)
        if not ziel_ordner:
            return
        try:
            ordner, anzahl, ohne = herausgeben(basis, ziel_ordner)
        except Exception as fehler:
            messagebox.showerror(T("Herausgeben"), str(fehler))
            return
        messagebox.showinfo(
            T("Herausgeben"),
            T("Herausgegeben nach: %s") % ordner + "\n\n"
            + T("%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv "
                "und LIESMICH.txt.") % (anzahl, ohne) + "\n\n"
            + T("Die Karte wurde nur gelesen. Die Dateien brauchen dieses "
                "Werkzeug nicht: ein WAV ist ein WAV, und die Liste ist Text."))
    tk.Button(f, text=T("Alles herausgeben …"),
              command=herausgeben_jetzt).pack(pady=(6, 2))

    tk.Label(f, text=T("Sicherungen im Zielordner"), bg=FARBE_HINTERGRUND,
             fg=FARBE_SCHRIFT, font=schrift(12, fest=True)).pack(pady=(12, 2))

    # Sichern und Aufräumen an einer Stelle (19.11.2026): Vorher gab es zwei
    # Knöpfe im Hauptfenster; jetzt führt einer hierher, und das Anlegen einer
    # neuen Sicherung sitzt direkt daneben.
    def anderer_ordner():
        neu = ordner_waehlen(f, T("Ordner mit den Sicherungen"), start=ziel)
        if not neu or os.path.abspath(neu) == os.path.abspath(ziel):
            return
        einstellung_schreiben("sicherung_pfad", neu)
        f.destroy()
        fenster_aufraeumen(neu, haupfenster, sichern)

    tk.Button(f, text=T("Anderer Ordner …"), command=anderer_ordner).pack(pady=(0, 8))

    if sichern is not None:
        def neu_sichern():
            try:
                sichern()
            finally:
                f.destroy()
                fenster_aufraeumen(ziel, haupfenster, sichern)
        tk.Button(f, text=T("Neue Sicherung anlegen (alles sichern)"),
                  command=neu_sichern).pack(pady=(0, 8))
    tk.Label(f, text=ziel, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9), wraplength=660).pack(pady=(0, 8))

    rahmen = tk.Frame(f, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=16)

    anzeige = tk.Listbox(rahmen, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                         selectbackground=FARBE_AUSWAHL, selectmode="extended",
                         exportselection=False, font=schrift(10))
    anzeige.pack(side="left", fill="both", expand=True)
    roll = tk.Scrollbar(rahmen, command=anzeige.yview)
    roll.pack(side="right", fill="y")
    anzeige.config(yscrollcommand=roll.set)

    import time as _zeit
    einzigartig = unersetzliche_patches(laeufe)
    for _pfad, name, wann, groesse in laeufe:
        zusatz = ""
        if name == "vor-ueberschreiben":
            zusatz = "   " + T("(für Rückgängig nötig)")
        if einzigartig.get(name):
            zusatz += "   " + T("⟨%d Patch nur hier⟩") % einzigartig[name]
        anzeige.insert("end", "%s   %s   %6.1f MB%s" %
                       (_zeit.strftime("%d.%m.%Y %H:%M", _zeit.localtime(wann)),
                        name[:28], groesse / 1048576.0, zusatz))

    gesamt = sum(g for _, _, _, g in laeufe)
    kopf = tk.Label(f, text=T("%d Sicherungen · %.1f MB") % (len(laeufe), gesamt / 1048576.0),
                    bg=FARBE_HINTERGRUND, fg=FARBE_LEISE, font=schrift(9))
    kopf.pack(pady=(6, 2))

    def markieren_neueste(n):
        """Alles markieren AUSSER den neuesten n.

        Anlaß (19.11.2026): Die Knöpfe „Neueste behalten: 1 2 3 5" markierten
        die neuesten n — und markiert heißt in diesem Dialog „löschen". Wer
        „3" drückte, hätte also die drei neuesten Sicherungen gelöscht.
        """
        anzeige.selection_clear(0, "end")
        for i in range(min(n, anzeige.size()), anzeige.size()):
            anzeige.selection_set(i)

    zeile = tk.Frame(f, bg=FARBE_HINTERGRUND)
    zeile.pack(fill="x", padx=16, pady=(4, 0))
    marke_var = tk.StringVar(value="")
    def zaehlen(*_):
        indizes = list(anzeige.curselection())
        menge = sum(laeufe[i][3] for i in indizes) / 1048576.0
        marke_var.set(T("%d markiert · %.1f MB") % (len(indizes), menge) if indizes
                      else T("nichts markiert"))

    # Eine Zahl statt vier fester Knöpfe (19.11.2026): „1 2 3 5" war eine
    # willkürliche Staffel, und 4 fehlte. Jetzt steht dort, wie viele
    # Sicherungen bleiben sollen — jede Zahl ist möglich.
    tk.Label(zeile, text=T("Neueste behalten:"), bg=FARBE_HINTERGRUND,
             fg=FARBE_SCHRIFT).pack(side="left")
    behalten_var = tk.StringVar(value="3")
    behalten_feld = tk.Spinbox(zeile, from_=1, to=max(1, len(laeufe)),
                               width=4, justify="center", textvariable=behalten_var,
                               bg="#ffffff", fg="#000000", relief="solid",
                               borderwidth=1, font=schrift(11))
    behalten_feld.pack(side="left", padx=(4, 6))

    def aeltere_markieren():
        try:
            n = int(behalten_var.get())
        except (TypeError, ValueError):
            n = 3
        markieren_neueste(max(0, n))
        zaehlen()

    tk.Button(zeile, text=T("ältere markieren"),
              command=aeltere_markieren).pack(side="left")

    # Vorgabe für „was bleibt": die neuesten drei und alles aus den letzten
    # sieben Tagen. Sie markiert die übrigen — gelöscht wird erst nach der
    # Rückfrage.
    def vorgabe():
        import time as _zeit
        grenze = _zeit.time() - 7 * 24 * 3600
        anzeige.selection_clear(0, "end")
        for i, (_pfad, name, wann, _groesse) in enumerate(laeufe):
            if i < 3 or wann >= grenze:
                continue
            if einzigartig.get(name):
                # Hier liegen Patches, die es sonst nirgends gibt — die
                # Vorgabe läßt sie stehen (19.11.2026).
                continue
            anzeige.selection_set(i)
        zaehlen()

    tk.Button(zeile, text=T("Vorgabe: neueste 3 und letzte 7 Tage behalten"),
              command=vorgabe).pack(side="left", padx=(12, 2))
    anzeige.bind("<<ListboxSelect>>", zaehlen)
    tk.Label(f, textvariable=marke_var, bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9)).pack(pady=(4, 0))

    def loeschen():
        indizes = list(anzeige.curselection())
        if not indizes:
            messagebox.showinfo(T("Hinweis"), T("Erst Sicherungen in der Liste markieren."))
            return
        weg = [laeufe[i] for i in indizes]
        menge = sum(g for _, _, _, g in weg) / 1048576.0
        if any(name == "vor-ueberschreiben" for _, name, _, _ in weg) and not _frage_ja(f,
                T("Sicherungen aufräumen"),
                T("Darunter ist die Sicherung für Rückgängig. Danach läßt "
                  "sich der letzte Schreibvorgang nicht mehr zurücknehmen. "
                  "Trotzdem löschen?")):
            return
        if not _frage_ja(f,
                T("Sicherungen aufräumen"),
                T("%d Sicherungen mit %.1f MB werden endgültig gelöscht. Fortsetzen?")
                % (len(weg), menge)):
            return

        # Löschen im Hintergrund, Balken im Hauptfaden (19.11.2026: GB räumen
        # ließ das Fenster erstarren).
        def arbeit(melden):
            nicht_weg = 0
            for nummer, (pfad, name, _, _) in enumerate(weg, 1):
                melden((nummer - 1) / float(len(weg)),
                       T("%d von %d gelöscht: %s") % (nummer - 1, len(weg), name[:30]))
                if os.path.dirname(os.path.abspath(pfad)) != os.path.abspath(ziel):
                    nicht_weg += 1
                    continue
                try:
                    shutil.rmtree(pfad)
                except OSError:
                    nicht_weg += 1
            melden(1.0, T("%d von %d gelöscht")
                   % (len(weg) - nicht_weg, len(weg)))
            return nicht_weg

        try:
            fehler = fuehre_im_hintergrund(f, T("Sicherungen löschen"), arbeit)
        except Exception:
            fehler = len(weg)
        if fehler:
            messagebox.showwarning(T("Aufräumen"),
                                   T("%d Sicherungen konnten nicht gelöscht werden.") % fehler)
        f.destroy()
        fenster_aufraeumen(ziel, haupfenster)

    def retten():
        def arbeit(melden):
            return sammelsicherung(ziel, laeufe, melden=melden)
        try:
            ziel_neu, anzahl = fuehre_im_hintergrund(
                f, T("Sammelsicherung"), arbeit)
        except Exception as fehler:
            messagebox.showerror(T("Fehler"), str(fehler))
            return
        if not anzahl:
            messagebox.showinfo(T("Sammelsicherung"),
                                T("Es gibt keine unersetzlichen Patches."))
            return
        messagebox.showinfo(
            T("Sammelsicherung"),
            T("%d Patches wurden nach %s kopiert. Die Zuordnung steht dort in "
              "Rettung.txt. Jetzt lassen sich die alten Sicherungen löschen.")
            % (anzahl, ziel_neu))
        f.destroy()
        fenster_aufraeumen(ziel, haupfenster)

    if any(einzigartig.get(name) for _, name, _, _ in laeufe):
        tk.Button(f, text=T("Nur die unersetzlichen Patches retten"),
                  command=retten).pack(pady=(10, 2))

    tk.Button(f, text=T("Markierte löschen"), command=loeschen).pack(pady=(10, 4))
    tk.Button(f, text=T("Schließen"), command=f.destroy).pack(pady=(0, 12))


def datei_auswahl(haupfenster, titel, muster, start=None, nur_ordner=False,
                  vorgabe_name=None):
    """Eigener Dateiauswahl-Dialog: immer im Vordergrund, ohne Systemdialog.

    Anlaß (17.11.2026): zenity erschien hinter dem Werkzeug (das Fenster steht
    auf -topmost), und der Tk-Dialog war unübersichtlich. Dieser Dialog gehört
    zum Werkzeug, steht deshalb davor und blendet versteckte Einträge aus.

    Rückgabe: vollständiger Pfad oder "" (abgebrochen).
    """
    import tkinter as tk
    from tkinter import messagebox

    endungen = [m.lstrip("*").lower() for m in muster.split() if m.startswith("*.")]
    def lesbar(pfad):
        """Kann dieser Ordner überhaupt aufgelistet werden?"""
        try:
            os.listdir(pfad)
            return True
        except OSError:
            return False

    ordner = os.path.abspath(os.path.expanduser(start)) if start else os.path.expanduser("~")
    if not os.path.isdir(ordner) or not lesbar(ordner):
        # Nicht lesbar (Windows: „Zugriff verweigert" bei verknüpften
        # Bibliotheksordnern, 17.11.2026) — dann etwas Nehmbares vorschlagen.
        for ersatz in (os.path.join(os.path.expanduser("~"), "Music"),
                       os.path.join(os.path.expanduser("~"), "Musik"),
                       os.path.expanduser("~")):
            if os.path.isdir(ersatz) and lesbar(ersatz):
                ordner = ersatz
                break
        else:
            ordner = os.path.expanduser("~")

    fenster = tk.Toplevel(haupfenster)
    fenster.title(titel)
    fenster.configure(bg=FARBE_HINTERGRUND)
    fenster.geometry("960x560")
    fenster.minsize(700, 420)
    wahl = {"pfad": ""}

    # Pfad als Eingabefeld: unter Linux liegen Karten unter /media/<Benutzer>/…,
    # das tippt niemand gern von Hand nach.
    pfad_zeile = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    pfad_zeile.pack(fill="x", padx=12, pady=(10, 4))
    kopf_var = tk.StringVar(value="")
    kopf = tk.Entry(pfad_zeile, textvariable=kopf_var, bg=FARBE_LISTE,
                    fg=FARBE_SCHRIFT, insertbackground=FARBE_SCHRIFT,
                    relief="flat", font=schrift(10))
    kopf.pack(side="left", fill="x", expand=True, ipady=3)

    zustand_text = tk.StringVar(value="")
    tk.Label(fenster, textvariable=zustand_text, bg=FARBE_HINTERGRUND, fg="#d98c4a",
             font=schrift(9), anchor="w", justify="left",
             wraplength=880).pack(fill="x", padx=12)

    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=12, pady=(0, 6))

    inhalt = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    inhalt.pack(fill="both", expand=True, padx=12)

    links = tk.Frame(inhalt, bg=FARBE_HINTERGRUND, width=340)
    links.pack(side="left", fill="both", expand=True)
    links.pack_propagate(False)
    tk.Label(links, text=T("Ordner"), bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9), anchor="w").pack(fill="x")
    ordner_liste = tk.Listbox(links, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                              selectbackground=FARBE_AUSWAHL, relief="flat",
                              font=schrift(11), activestyle="none")
    ordner_liste.pack(fill="both", expand=True, pady=(2, 0))

    rechts = tk.Frame(inhalt, bg=FARBE_HINTERGRUND)
    rechts.pack(side="left", fill="both", expand=True, padx=(10, 0))
    tk.Label(rechts, text=T("Dateien"), bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9), anchor="w").pack(fill="x")
    datei_liste = tk.Listbox(rechts, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                             selectbackground=FARBE_AUSWAHL, relief="flat",
                             font=schrift(11), activestyle="none")
    datei_liste.pack(fill="both", expand=True, pady=(2, 0))

    unten = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    unten.pack(fill="x", padx=12, pady=(8, 12))
    tk.Label(unten, text=T("Auswahl:"), bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(9)).pack(side="left", padx=(0, 8))
    pfad_var = tk.StringVar(value="")
    tk.Entry(unten, textvariable=pfad_var, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
             insertbackground=FARBE_SCHRIFT, relief="flat",
             font=schrift(11)).pack(side="left", fill="x", expand=True, ipady=4)

    def gehe_zu(ereignis=None):
        ziel = os.path.abspath(os.path.expanduser(kopf_var.get().strip()))
        if os.path.isdir(ziel):
            fuellen(ziel)

    kopf.bind("<Return>", gehe_zu)

    def fuellen(pfad):
        kopf_var.set(pfad)
        ordner_liste.delete(0, "end")
        datei_liste.delete(0, "end")
        try:
            eintraege = sorted(os.listdir(pfad))
        except OSError as fehler:
            # Kein Abbruch: Hinweis zeigen, Listen leeren, „Nach oben" bleibt.
            zustand_text.set(T("Dieser Ordner ist nicht lesbar: %s") % fehler)
            return
        zustand_text.set("")
        if os.path.dirname(pfad) != pfad:
            ordner_liste.insert("end", "..")
        for name in eintraege:
            if name.startswith("."):
                continue
            voll = os.path.join(pfad, name)
            if os.path.isdir(voll):
                ordner_liste.insert("end", name + "/")
            elif not nur_ordner and any(name.lower().endswith(e) for e in endungen):
                datei_liste.insert("end", name)
        fenster.pfad = pfad

    def hinein(ereignis=None):
        gewaehlt = ordner_liste.curselection()
        if not gewaehlt:
            return
        name = ordner_liste.get(gewaehlt[0])
        ziel = (os.path.dirname(fenster.pfad) if name == ".."
                else os.path.join(fenster.pfad, name.rstrip("/")))
        if os.path.isdir(ziel):
            fuellen(ziel)

    def markiere(ereignis=None):
        gewaehlt = datei_liste.curselection()
        if gewaehlt:
            pfad_var.set(os.path.join(fenster.pfad, datei_liste.get(gewaehlt[0])))
            return
        # Ein markierter ORDNER soll ebenfalls sichtbar sein — sonst weiß man
        # nicht, was „Öffnen" gerade nehmen wird (19.11.2026).
        gewaehlt = ordner_liste.curselection()
        if gewaehlt:
            name = ordner_liste.get(gewaehlt[0]).rstrip("/")
            if name != "..":
                pfad_var.set(os.path.join(fenster.pfad, name))

    name_eingabe = None
    if vorgabe_name is not None:
        name_zeile = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
        name_zeile.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(name_zeile, text=T("Dateiname:"), bg=FARBE_HINTERGRUND,
                 fg=FARBE_LEISE).pack(side="left")
        name_var = tk.StringVar(value=vorgabe_name)
        name_eingabe = tk.Entry(name_zeile, textvariable=name_var,
                                bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                                insertbackground=FARBE_SCHRIFT, relief="flat",
                                font=schrift(11))
        name_eingabe.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

    def oeffnen():
        if vorgabe_name is not None:
            name = name_var.get().strip()
            if not name:
                messagebox.showinfo(T("Hinweis"), T("Bitte einen Dateinamen angeben."),
                                    parent=fenster)
                return
            ziel = os.path.join(fenster.pfad, name)
            if os.path.exists(ziel) and not frage_ja_nein(
                    fenster, T("Überschreiben"), T("%s gibt es schon. Ersetzen?") % ziel):
                return
            wahl["pfad"] = ziel
            fenster.destroy()
            return
        if nur_ordner:
            # Anlaß (19.11.2026): Wer im Ordnerdialog „JamMan-Sicherung"
            # markierte und „Öffnen" drückte, bekam das Benutzerverzeichnis —
            # der Dialog gab immer den offenen Ordner zurück. Jetzt gilt:
            # markierter Ordner → dieser; nichts markiert → der offene.
            gewaehlt = ordner_liste.curselection()
            if gewaehlt:
                name = ordner_liste.get(gewaehlt[0]).rstrip("/")
                if name != "..":
                    ziel = os.path.join(fenster.pfad, name)
                    if os.path.isdir(ziel):
                        wahl["pfad"] = ziel
                        fenster.destroy()
                        return
            wahl["pfad"] = fenster.pfad
            fenster.destroy()
            return
        pfad = pfad_var.get().strip()
        if pfad and os.path.isfile(pfad):
            wahl["pfad"] = pfad
            fenster.destroy()
        else:
            messagebox.showinfo(T("Hinweis"), T("Bitte eine Datei auswählen."),
                                parent=fenster)

    def hoch():
        fuellen(os.path.dirname(fenster.pfad))

    tk.Button(leiste, text=T("Nach oben"), command=hoch).pack(side="left")
    tk.Button(leiste, text=T("Speichern") if vorgabe_name is not None else T("Öffnen"),
              command=oeffnen).pack(side="right")
    tk.Button(leiste, text=T("Abbrechen"),
              command=fenster.destroy).pack(side="right", padx=(0, 8))

    ordner_liste.bind("<<ListboxSelect>>", markiere)
    ordner_liste.bind("<Double-Button-1>", hinein)
    ordner_liste.bind("<Return>", hinein)
    datei_liste.bind("<<ListboxSelect>>", markiere)
    datei_liste.bind("<Double-Button-1>", lambda e: oeffnen())
    datei_liste.bind("<Return>", lambda e: oeffnen())

    fuellen(ordner)

    # Das Elternfenster (USB-Fenster) hält sich mit -topmost nach vorn und
    # springt sonst über diesen Dialog (17.11.2026). Also für die Dauer des
    # Dialogs zurücknehmen und danach wiederherstellen.
    eltern_vorne = False
    try:
        if haupfenster is not None:
            eltern_vorne = bool(haupfenster.attributes("-topmost"))
            if eltern_vorne:
                haupfenster.attributes("-topmost", False)
    except Exception:
        eltern_vorne = False

    try:
        fenster.attributes("-topmost", True)
        fenster.lift()
        fenster.focus_force()
        fenster.grab_set()
        # Manche Fenstermanager stellen das Elternfenster erst danach wieder
        # nach vorn — ein zweites Anheben kurz darauf hilft.
        fenster.after(120, lambda: (fenster.lift(), fenster.focus_force()))
    except Exception:
        pass

    haupfenster.wait_window(fenster)
    if eltern_vorne:
        try:
            haupfenster.attributes("-topmost", True)
        except Exception:
            pass
    return wahl["pfad"]


def ordner_waehlen(haupfenster, titel, start=None):
    """Ordner auswählen — mit dem eigenen Dialog (siehe datei_auswahl)."""
    return datei_auswahl(haupfenster, titel, "", start, nur_ordner=True)


def datei_speichern(haupfenster, titel, vorgabe_name, start=None, muster=""):
    """Dateiname und Ordner wählen — eigener Dialog, siehe datei_auswahl."""
    return datei_auswahl(haupfenster, titel, muster, start,
                         vorgabe_name=vorgabe_name)


def datei_waehlen(haupfenster, titel, muster, start=None, endung=".txt"):
    """Datei auswählen — über den eigenen Dialog des Werkzeugs.

    Anlaß (17.11.2026): Der Tk-Dialog war unübersichtlich (enge Spalten, alle
    versteckten Dateien), und zenity erschien hinter dem Fenster, weil das
    Werkzeug auf -topmost steht. Der eigene Dialog gehört zum Werkzeug und
    steht damit zwangsläufig davor.
    """
    return datei_auswahl(haupfenster, titel, muster, start)


def sperrbare_widgets(wurzel, ausnahmen=()):
    """Alle Knöpfe und Menüs unter `wurzel`, außer den genannten Beschriftungen.

    Wird gebraucht, damit während eines Hole- oder Schreibvorgangs nichts
    dazwischenfunkt (17.11.2026).
    """
    treffer = []

    def lauf(widget):
        for kind in widget.winfo_children():
            art = kind.winfo_class()
            if art in ("Button", "Menubutton", "Radiobutton"):
                beschriftung = ""
                if art != "Radiobutton":
                    try:
                        beschriftung = str(kind.cget("text"))
                    except Exception:
                        beschriftung = ""
                if beschriftung not in ausnahmen:
                    treffer.append(kind)
            lauf(kind)

    lauf(wurzel)
    return treffer


def beschreibung_fenster(haupfenster, pfad):
    """Name, Interpret, Genre, Bewertung, Stichwörter, Text und Tempo ändern.

    Diese Felder stehen im patch.xml/phrase.xml des Pedals, hatten aber keine
    Oberfläche (17.11.2026: JamManager XT kann sie, das Werkzeug schrieb immer
    „Unspecified“, Bewertung 0 und 90 BPM).
    """
    import tkinter as tk
    from tkinter import messagebox

    ist = patch_beschreibung(pfad)
    fenster = tk.Toplevel(haupfenster)
    fenster.title(T("Beschreibung"))
    fenster.configure(bg=FARBE_HINTERGRUND)
    fenster.resizable(False, False)

    eltern_vorne = False
    try:
        eltern_vorne = bool(haupfenster.attributes("-topmost"))
        if eltern_vorne:
            haupfenster.attributes("-topmost", False)
    except Exception:
        eltern_vorne = False

    felder = {}
    zeilen = [
        ("name", T("Name"), 40),
        ("artist", T("Interpret"), 40),
        ("genre", T("Genre"), 24),
        ("tags", T("Stichwörter"), 40),
        ("description", T("Beschreibung"), 56),
    ]
    for schluessel, beschriftung, breite in zeilen:
        zeile = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
        zeile.pack(fill="x", padx=16, pady=3)
        tk.Label(zeile, text=beschriftung + ":", width=13, anchor="w",
                 bg=FARBE_HINTERGRUND, fg=FARBE_LEISE).pack(side="left")
        var = tk.StringVar(value=str(ist.get(schluessel) or ""))
        tk.Entry(zeile, textvariable=var, width=breite, bg=FARBE_LISTE,
                 fg=FARBE_SCHRIFT, insertbackground=FARBE_SCHRIFT,
                 relief="flat").pack(side="left", ipady=3)
        felder[schluessel] = var

    unten = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    unten.pack(fill="x", padx=16, pady=(6, 14))

    tk.Label(unten, text=T("Bewertung") + ":", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    bewertung = tk.StringVar(value=str(ist.get("rating") or "0"))
    tk.Spinbox(unten, from_=0, to=5, width=4, textvariable=bewertung,
               bg=FARBE_LISTE, fg=FARBE_SCHRIFT, relief="flat",
               insertbackground=FARBE_SCHRIFT).pack(side="left", padx=(6, 18))

    tk.Label(unten, text=T("Tempo (BPM)") + ":", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left")
    tempo = tk.StringVar(value=("%.1f" % ist["bpm"]) if ist.get("bpm") else "")
    tk.Spinbox(unten, from_=30, to=300, width=6, textvariable=tempo,
               bg=FARBE_LISTE, fg=FARBE_SCHRIFT, relief="flat",
               insertbackground=FARBE_SCHRIFT).pack(side="left", padx=(6, 18))

    def speichern():
        werte = {k: v.get().strip() for k, v in felder.items()}
        werte["rating"] = (bewertung.get().strip() or "0")
        try:
            werte["bpm"] = float(tempo.get().replace(",", "."))                 if tempo.get().strip() else None
        except ValueError:
            werte["bpm"] = None
        try:
            patch_beschreibung_setzen(pfad, werte)
        except Exception as e:
            messagebox.showerror(T("Fehler"), str(e), parent=fenster)
            return
        fenster.destroy()

    tk.Button(unten, text=T("Speichern"), command=speichern).pack(side="right")
    tk.Button(unten, text=T("Abbrechen"),
              command=fenster.destroy).pack(side="right", padx=(0, 8))

    fenster.bind("<Return>", lambda e: speichern())
    fenster.bind("<Escape>", lambda e: fenster.destroy())
    try:
        fenster.attributes("-topmost", True)
        fenster.lift()
        fenster.focus_force()
        fenster.grab_set()
    except Exception:
        pass
    haupfenster.wait_window(fenster)
    if eltern_vorne:
        try:
            haupfenster.attributes("-topmost", True)
        except Exception:
            pass


def _frage_auswahl(haupfenster, titel, moeglichkeiten):
    """Eine von mehreren Möglichkeiten wählen; gibt den Index oder None."""
    import tkinter as tk

    fenster = tk.Toplevel(haupfenster)
    fenster.title(titel)
    fenster.configure(bg=FARBE_HINTERGRUND)
    eltern_vorne = False
    try:
        eltern_vorne = bool(haupfenster.attributes("-topmost"))
        if eltern_vorne:
            haupfenster.attributes("-topmost", False)
    except Exception:
        eltern_vorne = False
    auswahl = {"index": None}
    liste = tk.Listbox(fenster, height=min(12, max(3, len(moeglichkeiten))),
                       width=44, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                       selectbackground=FARBE_AUSWAHL, relief="flat",
                       exportselection=False)
    for wert in moeglichkeiten:
        liste.insert("end", wert)
    liste.pack(padx=16, pady=12)
    liste.selection_set(0)
    liste.focus_set()

    def uebernehmen(*_):
        gewaehlt = liste.curselection()
        if gewaehlt:
            auswahl["index"] = gewaehlt[0]
        fenster.destroy()

    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=16, pady=(0, 12))
    tk.Button(leiste, text=T("Abbrechen"), command=fenster.destroy).pack(side="right")
    tk.Button(leiste, text=T("\u00dcbernehmen"),
              command=uebernehmen).pack(side="right", padx=(0, 8))
    liste.bind("<Double-Button-1>", uebernehmen)
    fenster.bind("<Return>", uebernehmen)
    fenster.bind("<Escape>", lambda e: fenster.destroy())
    try:
        fenster.attributes("-topmost", True)
        fenster.lift()
        fenster.grab_set()
    except Exception:
        pass
    haupfenster.wait_window(fenster)
    if eltern_vorne:
        try:
            haupfenster.attributes("-topmost", True)
        except Exception:
            pass
    return auswahl["index"]


def frage_ja_nein_mit_haken(haupfenster, titel, text, haken_text, haken_wert):
    """Ja/Nein-Frage mit einem Häkchen darunter.

    Anlaß (19.11.2026): Der Nutzer erwartete die Einstellung „Selbsttest nach
    Update" beim Selbsttest-Knopf, nicht in den Voreinstellungen. Jetzt steht
    sie an beiden Stellen; dieses Häkchen schreibt dieselbe Einstellung.
    Rückgabe: (ja, haken_an)
    """
    import tkinter as tk

    fenster = tk.Toplevel(haupfenster)
    fenster.title(titel)
    fenster.configure(bg=FARBE_HINTERGRUND)
    fenster.resizable(False, False)
    var = tk.BooleanVar(value=bool(haken_wert))
    antwort = {"ja": False}

    tk.Label(fenster, text=text, bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT,
             font=schrift(11), wraplength=520, justify="left").pack(
        padx=18, pady=(18, 10))
    tk.Checkbutton(fenster, text=haken_text, variable=var,
                   bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                   activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(anchor="w", padx=18,
                                                       pady=(0, 10))

    def schliessen(ja):
        antwort["ja"] = ja
        fenster.destroy()

    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=18, pady=(0, 16))
    tk.Button(leiste, text=T("Nein"), width=10,
              command=lambda: schliessen(False)).pack(side="right")
    tk.Button(leiste, text=T("Ja"), width=10,
              command=lambda: schliessen(True)).pack(side="right", padx=(0, 8))
    fenster.bind("<Return>", lambda e: schliessen(True))
    fenster.bind("<Escape>", lambda e: schliessen(False))
    try:
        fenster.grab_set()
        fenster.transient(haupfenster)
        fenster.wait_window()
    except Exception:
        pass
    return antwort["ja"], bool(var.get())


def frage_ja_nein(haupfenster, titel, text):
    """Ja/Nein-Frage mit eigenen Knöpfen.

    Anlaß (17.11.2026): Die Systemdialoge beschriften ihre Knöpfe selbst
    („Yes“/„No“), während der Text deutsch war. Hier stehen T("Ja") und
    T("Nein"), in jeder Sprache.
    """
    import tkinter as tk

    fenster = tk.Toplevel(haupfenster)
    fenster.title(titel)
    fenster.configure(bg=FARBE_HINTERGRUND)
    fenster.resizable(False, False)
    antwort = {"ja": False}

    eltern_vorne = False
    try:
        eltern_vorne = bool(haupfenster.attributes("-topmost"))
        if eltern_vorne:
            haupfenster.attributes("-topmost", False)
    except Exception:
        eltern_vorne = False

    tk.Label(fenster, text=text, bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT,
             font=schrift(11), wraplength=520, justify="left").pack(
        padx=18, pady=(18, 12))

    def schliessen(ja):
        antwort["ja"] = ja
        fenster.destroy()

    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=18, pady=(0, 16))
    tk.Button(leiste, text=T("Nein"), width=10,
              command=lambda: schliessen(False)).pack(side="right")
    tk.Button(leiste, text=T("Ja"), width=10,
              command=lambda: schliessen(True)).pack(side="right", padx=(0, 8))

    fenster.bind("<Return>", lambda e: schliessen(True))
    fenster.bind("<Escape>", lambda e: schliessen(False))
    try:
        fenster.attributes("-topmost", True)
        fenster.lift()
        fenster.focus_force()
        fenster.grab_set()
    except Exception:
        pass

    haupfenster.wait_window(fenster)
    if eltern_vorne:
        try:
            haupfenster.attributes("-topmost", True)
        except Exception:
            pass
    return antwort["ja"]


def _anleitung_fenster(haupfenster, text, titel=None):
    """Langen Hilfetext in einem eigenen Fenster: scrollbar, markierbar, kopierbar.

    Anlaß (17.11.2026): Der ffmpeg-Text landete in der Statuszeile des
    USB-Fensters — abgeschnitten, und Befehle ließen sich nicht kopieren.
    """
    import tkinter as tk

    titel = titel or T("Anleitung")
    fenster = tk.Toplevel(haupfenster)
    fenster.title(titel)
    fenster.configure(bg=FARBE_HINTERGRUND)
    fenster.geometry("780x560")
    fenster.minsize(560, 360)

    # Das Elternfenster hält sich mit -topmost nach vorn. Ohne Gegenmaßnahme
    # läge dieses Fenster dahinter (17.11.2026 die gleiche Ursache wie beim
    # Hinweis-Dialog). Also: Eltern kurz zurücknehmen, dieses Fenster nach
    # vorn, beim Schließen alles zurückgeben.
    eltern_vorne = False
    try:
        eltern_vorne = bool(haupfenster.attributes("-topmost"))
        if eltern_vorne:
            haupfenster.attributes("-topmost", False)
    except Exception:
        eltern_vorne = False
    try:
        fenster.attributes("-topmost", True)
    except Exception:
        pass

    def schliessen():
        try:
            fenster.destroy()
        finally:
            if eltern_vorne:
                try:
                    haupfenster.attributes("-topmost", True)
                except Exception:
                    pass

    fenster.protocol("WM_DELETE_WINDOW", schliessen)

    rahmen = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=14, pady=(14, 8))
    rolle = tk.Scrollbar(rahmen)
    rolle.pack(side="right", fill="y")
    bereich = tk.Text(rahmen, wrap="word", yscrollcommand=rolle.set,
                      bg=FARBE_LISTE, fg=FARBE_SCHRIFT, insertbackground=FARBE_SCHRIFT,
                      relief="flat", padx=12, pady=10,
                      font=("Menlo", 11) if MAC else ("Consolas", 10))
    bereich.pack(side="left", fill="both", expand=True)
    rolle.config(command=bereich.yview)
    bereich.insert("1.0", text)
    # Nur lesen, aber bedienbar: Ein gesperrtes Textfeld ("disabled") nimmt
    # auch keine Kopier-Taste an (17.11.2026 nachgemessen). Deshalb bleibt es
    # bedienbar, und Tippen wird abgewiesen — Kopieren und Blättern nicht.
    def nur_lesen(ereignis):
        if ereignis.state & 0x4 or ereignis.state & 0x8 or ereignis.state & 0x10:
            return None                      # Strg/Cmd: kopieren, alles markieren
        if ereignis.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                               "Prior", "Next", "Shift_L", "Shift_R",
                               "Control_L", "Control_R", "Command"):
            return None                      # Blättern und Markieren
        return "break"                       # alles andere abweisen

    bereich.bind("<Key>", nur_lesen)

    def kopiere(ereignis=None):
        """Auswahl kopieren — eigene Bindung, unabhängig vom Systemdialog.

        Die Standardbindung des Textfelds greift nicht zuverlässig, wenn das
        Feld den Fokus nicht hat (17.11.2026 nachgemessen).
        """
        try:
            auswahl = bereich.get("sel.first", "sel.last")
        except tk.TclError:
            auswahl = bereich.get("1.0", "end-1c")
        try:
            fenster.clipboard_clear()
            fenster.clipboard_append(auswahl)
            melde_kurz(T("In die Zwischenablage kopiert."))
        except Exception as fehler:
            melde_kurz(str(fehler))
        return "break"

    for folge in ("<Command-c>", "<Command-C>", "<Control-c>", "<Control-C>"):
        bereich.bind(folge, kopiere)
    bereich.focus_set()

    leiste = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    leiste.pack(fill="x", padx=14, pady=(0, 12))
    zustand = tk.Label(leiste, text="", bg=FARBE_HINTERGRUND, fg=FARBE_TON)

    def melde_kurz(text):
        zustand.config(text=text)
        zustand.pack(side="left", padx=(12, 0))

    def kopieren(nur_auswahl=False):
        try:
            if nur_auswahl:
                try:
                    inhalt = bereich.get("sel.first", "sel.last")
                except tk.TclError:
                    melde_kurz(T("Nichts markiert — bitte zuerst Text markieren."))
                    return
            else:
                inhalt = bereich.get("1.0", "end-1c")
            fenster.clipboard_clear()
            fenster.clipboard_append(inhalt)
            melde_kurz(T("In die Zwischenablage kopiert."))
        except Exception as fehler:
            melde_kurz(str(fehler))

    def als_datei():
        ziel = datei_speichern(fenster, T("Anleitung speichern"),
                               "ffmpeg-anleitung.txt", None, "*.txt")
        if not ziel:
            return
        try:
            with open(ziel, "w", encoding="utf-8") as datei:
                datei.write(bereich.get("1.0", "end-1c"))
            melde_kurz(T("Gespeichert."))
        except OSError as fehler:
            melde_kurz(str(fehler))

    tk.Button(leiste, text=T("Auswahl kopieren"),
              command=lambda: kopieren(True)).pack(side="left")
    tk.Button(leiste, text=T("Alles kopieren"),
              command=lambda: kopieren(False)).pack(side="left", padx=(8, 0))
    tk.Button(leiste, text=T("Als Datei speichern"),
              command=als_datei).pack(side="left", padx=(8, 0))
    tk.Button(leiste, text=T("Schließen"), command=schliessen).pack(side="right")

    try:
        fenster.lift()
        fenster.focus_force()
    except Exception:
        pass
    return fenster


def _hinweis(fenster, text, titel=None):
    """Hinweis-Dialog, der nicht hinter dem eigenen Fenster verschwindet.

    Anlaß (17.11.2026): Im USB-Fenster (bleibt im Vordergrund) erschien der
    Hinweis zum Zielplatz dahinter — der Nutzer sah nur den Fehler, nicht die
    Erklärung. Hier wird das „immer im Vordergrund" für die Dauer des Dialogs
    abgeschaltet und der Dialog als Kind des Fensters gezeigt.
    """
    from tkinter import messagebox

    titel = titel or T("Hinweis")
    war_vorne = False
    if fenster is not None:
        try:
            war_vorne = bool(fenster.attributes("-topmost"))
            if war_vorne:
                fenster.attributes("-topmost", False)
        except Exception:
            war_vorne = False
    try:
        if fenster is not None:
            messagebox.showinfo(titel, text, parent=fenster)
        else:
            messagebox.showinfo(titel, text)
    finally:
        if fenster is not None and war_vorne:
            try:
                fenster.attributes("-topmost", True)
            except Exception:
                pass


def _platz_pruefen(fenster, platz):
    """Prüft die Eingabe des Zielplatzes und sagt, was erwartet wird.

    Am 16.11.2026 blieb ein leerer oder falscher Platz ohne jede Meldung —
    es sah aus, als sei der Knopf kaputt.
    """
    import re as _re
    if not platz:
        _hinweis(fenster, T("Es wurde kein Zielplatz eingegeben."))
        return None
    # Tolerant lesen: Leerzeichen, Anführungszeichen, vorangestellte
    # Sonderzeichen (am 17.11.2026 kam „_Patch17" an) und ein bloßes „17".
    platz = str(platz).strip().strip("\"'\u201e\u201c\u2018\u2019").rstrip("/")
    platz = platz.lstrip("_.- ")
    treffer = _re.match(r"^Patch\s*(\d{1,3})$", platz, _re.IGNORECASE)
    if not treffer:
        treffer = _re.match(r"^(\d{1,3})$", platz)
    if not treffer or not (1 <= int(treffer.group(1)) <= 200):
        _hinweis(fenster, T("Der Zielplatz muß wie Patch01 … Patch200 heißen, "
                            "nicht „%s“.") % platz)
        return None
    nummer = int(treffer.group(1))
    return "Patch%02d" % nummer


def _frage_text(haupfenster, titel, frage, vorgabe=""):
    """Eigene Eingabe — ohne simpledialog und ohne angehängten Dialog.

    Anlaß (16.11.2026): Im USB-Fenster ließ sich kein Zielplatz eingeben.
    Der Systemdialog schloß sofort, und das Fenster stand im Vordergrund
    (`-topmost`), was das Zusammenspiel mit einem angehängten Dialog
    („sheet") zerstört. Diese Fassung nimmt dem Elternfenster für die Dauer
    der Eingabe das „immer im Vordergrund" und bringt den Dialog selbst nach
    vorn — ohne transient, ohne Systemdialog.
    """
    import tkinter as tk

    ergebnis = {"wert": None}
    vorne = True
    try:
        vorne = bool(haupfenster.attributes("-topmost"))
        haupfenster.attributes("-topmost", False)
    except Exception:
        vorne = False

    d = tk.Toplevel(haupfenster)
    d.title(titel)
    d.configure(bg=FARBE_HINTERGRUND)
    d.resizable(False, False)
    try:
        d.attributes("-topmost", True)
    except Exception:
        pass
    d.geometry("470x180+%d+%d" % (haupfenster.winfo_rootx() + 80,
                                  haupfenster.winfo_rooty() + 90))

    tk.Label(d, text=frage, bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT,
             font=schrift(10), wraplength=430, justify="left").pack(padx=16, pady=(14, 8))
    feld = tk.Entry(d, width=32, font=schrift(13))
    feld.insert(0, vorgabe)
    feld.pack(padx=16, pady=(0, 10))
    feld.select_range(0, "end")
    feld.icursor("end")

    def aufraeumen():
        try:
            haupfenster.attributes("-topmost", vorne)
        except Exception:
            pass

    def uebernehmen(*_a):
        ergebnis["wert"] = feld.get().strip()
        aufraeumen()
        d.destroy()

    def abbrechen(*_a):
        ergebnis["wert"] = None
        aufraeumen()
        d.destroy()

    zeile = tk.Frame(d, bg=FARBE_HINTERGRUND)
    zeile.pack(pady=(0, 12))
    tk.Button(zeile, text=T("OK"), command=uebernehmen, width=10).pack(side="left", padx=6)
    tk.Button(zeile, text=T("Abbrechen"), command=abbrechen, width=10).pack(side="left", padx=6)

    # Nur das Feld hört auf Return/Escape — nicht das ganze Fenster.
    feld.bind("<Return>", uebernehmen)
    feld.bind("<KP_Enter>", uebernehmen)
    feld.bind("<Escape>", abbrechen)
    d.protocol("WM_DELETE_WINDOW", abbrechen)
    d.grab_set()
    d.lift()
    feld.focus_force()
    haupfenster.wait_window(d)
    return ergebnis["wert"]


def _usb_fenster_bauen(haupfenster, g_modul, oeffne_karte=None):
    """Der eigentliche Aufbau des USB-Fensters."""
    import queue
    import threading
    import tkinter as tk
    from tkinter import messagebox

    f = tk.Toplevel(haupfenster)
    f.title(T("Pedal über USB"))
    f.configure(bg=FARBE_HINTERGRUND)
    # Über dem Hauptfenster und in dessen Mitte: sonst öffnet es sich
    # irgendwo und wirkt, als sei nichts passiert (16.11.2026).
    try:
        # KEIN transient(): auf macOS nimmt das Hauptfenster dann keine Klicks
        # mehr an, solange das USB-Fenster offen ist (16.11.2026).
        haupfenster.update_idletasks()
        bx, by = haupfenster.winfo_rootx(), haupfenster.winfo_rooty()
        bw, bh = haupfenster.winfo_width(), haupfenster.winfo_height()
        f.geometry("760x620+%d+%d" % (bx + max(0, (bw - 760) // 2),
                                      by + max(0, (bh - 620) // 2)))
    except Exception:
        f.geometry("760x620")
    f.lift()
    try:
        f.focus_force()
        # bleibt im Vordergrund: vorher verschwand es nach 1,2 s hinter dem
        # großen Hauptfenster und wirkte wie geschlossen (16.11.2026)
        f.attributes("-topmost", True)
    except Exception:
        pass
    _USB_FENSTER["fenster"] = f

    kopf = tk.Label(f, text=T("Verbinde mit dem Pedal …"), bg=FARBE_HINTERGRUND,
                    fg=FARBE_SCHRIFT, font=schrift(11, fest=True),
                    wraplength=720, justify="center")
    kopf.pack(pady=(10, 4))

    wahl = tk.StringVar(value="intern")
    zeile = tk.Frame(f, bg=FARBE_HINTERGRUND)
    zeile.pack(pady=(0, 6))
    for text, wert in ((T("Interner Speicher"), "intern"), (T("SD-Karte"), "sd")):
        tk.Radiobutton(zeile, text=text, value=wert, variable=wahl,
                       command=lambda: anstossen(), bg=FARBE_HINTERGRUND,
                       fg=FARBE_SCHRIFT, selectcolor=FARBE_LISTE,
                       activebackground=FARBE_HINTERGRUND).pack(side="left", padx=6)

    liste = tk.Listbox(f, bg=FARBE_LISTE, fg=FARBE_SCHRIFT, selectbackground=FARBE_AUSWAHL,
                       height=14, font=schrift(10), selectmode="extended",
                       exportselection=False)
    liste.pack(fill="both", expand=True, padx=14, pady=6)

    def alle_markieren(*_a):
        """Alles in der Liste markieren (Strg/Cmd+A).

        Vorher stand hier `alle_auswaehlen` — eine Funktion aus einem anderen
        Bereich. Der Aufruf scheiterte still im `except`, die Tastenkombination
        tat also nichts (19.11.2026 von `ruff` gefunden: F821).
        """
        try:
            liste.selection_set(0, "end")
        except Exception:
            pass
        return "break"

    for _taste in ("<Command-a>", "<Control-a>"):
        try:
            f.bind_all(_taste, alle_markieren, add="+")
        except Exception:
            pass

    hinweis = tk.Label(f, text="",
                       bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                       font=schrift(9), wraplength=560, justify="center")
    hinweis.pack(pady=(0, 4))

    stand = {"balken": None, "geraet": None, "basis": None,
             "patchliste": [], "laeuft": False,
             "abbruch": False, "t0": 0.0, "bytes": 0,
             "ziel": "", "ziel_eigen": False,
             "zu": False, "nach": None}
    meldungen = queue.Queue()

    # Alles, was während eines Laufs stören würde, wird gesperrt
    # (17.11.2026: Umschalten oder ein zweiter Lauf brachte zwei Stränge
    #  durcheinander). „Abbrechen“ und „Schließen“ bleiben bedienbar.
    sperrbar = []

    def sperren(an):
        for ding in sperrbar:
            try:
                ding.config(state="disabled" if an else "normal")
            except Exception:
                pass


    def melde(text):
        kopf.config(text=text)

    def anzeigen(patchliste):
        liste.delete(0, "end")
        for eintrag in patchliste:
            platz, name = eintrag[0], eintrag[1]
            dauer = eintrag[3] if len(eintrag) > 3 else 0.0
            stop = eintrag[4] if len(eintrag) > 4 else "—"
            loop = eintrag[6] if len(eintrag) > 6 else "—"
            dauer_txt = sekunden(dauer) if dauer else "—"
            liste.insert("end", "%-8s · %-28s · %8s · %-6s · %s" %
                         (platz, (name or T("(ohne Namen)"))[:28],
                          dauer_txt, stop, loop))
        # Ohne Gerät gibt es keinen Namen — das passiert, wenn die Verbindung
        # scheitert, die Liste aber trotzdem aufgebaut wird (Rückmeldung
        # 18.11.2026: AttributeError 'NoneType' object has no attribute 'kopf').
        geraet = stand.get("geraet")
        name = geraet.kopf.get("name") if geraet is not None else T("kein Pedal")
        melde("%s — %s" % (name,
                           T("Interner Speicher") if wahl.get() == "intern" else T("SD-Karte")))
        try:
            f.lift()
        except Exception:
            pass

    def balken_holen():
        """Fortschrittsfenster für die laufende Arbeit (19.11.2026)."""
        if stand.get("balken") is None and not stand["zu"]:
            stand["balken"] = Fortschritt(f, T("Pedal über USB"))
        return stand.get("balken")

    def balken_zu():
        b = stand.get("balken")
        if b is not None:
            b.schliessen()
            stand["balken"] = None

    def abholen():
        """Im Hauptstrang: Meldungen des Arbeitsstrangs abarbeiten."""
        if stand["zu"]:
            return
        try:
            while True:
                art, inhalt = meldungen.get_nowait()
                if art == "text":
                    melde(inhalt)
                elif art == "anteil":
                    anteil, text = inhalt
                    b = balken_holen()
                    if b is not None:
                        b.setzen(anteil, text)
                elif art == "setlists":
                    ort, zuege_fertig = inhalt
                    try:
                        kandidaten = [n for n in sorted(os.listdir(ort))
                                      if n.lower().endswith(".txt")]
                    except Exception:
                        kandidaten = []
                    if kandidaten and _frage_ja(
                            f, T("Setlists anpassen"),
                            T("%d Setlist-Dateien in %s an die neuen Plätze "
                              "anpassen? Jede Datei wird vorher daneben "
                              "gesichert.") % (len(kandidaten), ort)):
                        try:
                            geaendert = setlists_anpassen(ort, zuege_fertig)
                        except Exception as e:
                            meldungen.put(("text", T("Setlists nicht angepaßt: ")
                                           + str(e)))
                        else:
                            if geaendert:
                                meldungen.put(("text", T("Setlists angepaßt: ")
                                               + ", ".join("%s (%d Zeilen)" % g
                                                           for g in geaendert)))
                            else:
                                meldungen.put(("text", T("Keine Setlist nannte "
                                                         "einen bewegten Platz.")))
                elif art == "hinweis":
                    _hinweis(f, inhalt)
                elif art == "anleitung":
                    _anleitung_fenster(f, inhalt, T("Anleitung"))
                elif art == "liste":
                    anzeigen(inhalt)
                elif art == "markieren":
                    try:
                        liste.selection_clear(0, "end")
                        liste.selection_set(inhalt)
                        liste.see(inhalt)
                    except Exception:
                        pass
                elif art == "neu":
                    stand["laeuft"] = False
                    anstossen()
                elif art == "balken_zu":
                    balken_zu()
                elif art == "verbindung_neu":
                    # Nach einem Abbruch ist der Anschluß oft in einem
                    # Zwischenzustand; neu aufbauen spart den zweiten Fehler
                    # (19.11.2026: „Fehler beim Sichern" gleich nach Abbruch).
                    try:
                        verbindung_neu()
                    except Exception:
                        pass
                elif art == "aufraeumen":
                    ordner_pfad, dateien = inhalt
                    # Läuft schon ein neuer Hole-Lauf (z. B. nach „Verbindung
                    # neu aufbauen“), darf das Aufräumen des alten Laufs nicht
                    # dazwischenfahren — sonst verschwindet die .part-Datei,
                    # die der neue Lauf gerade schreibt (17.11.2026).
                    if not stand.get("laeuft") and dateien and _frage_ja(f,
                            T("Aufräumen"),
                            T("Die angefangene Kopie entfernen? Nur die gerade "
                              "geholten Dateien werden gelöscht, der Ordner bleibt.")):
                        weg = _raeume_auf(ordner_pfad, dateien)
                        melde(T("Aufgeräumt:") + " %d Dateien" % weg)
                elif art == "auffrischen":
                    if oeffne_karte:
                        try:
                            oeffne_karte(inhalt)
                        except Exception:
                            pass
                elif art == "oeffnen":
                    if oeffne_karte and _frage_ja(f, 
                            T("Pedal über USB"),
                            T("Im Hauptfenster öffnen?") + "\n" + inhalt):
                        try:
                            stand["geoeffnet"] = inhalt
                            oeffne_karte(inhalt)
                        except SystemExit as e:
                            messagebox.showerror(T("Fehler"), str(e))
                        except Exception as e:
                            messagebox.showerror(T("Fehler"), str(e))
                elif art == "fertig":
                    balken_zu()
                    sperren(False)
                    stand["laeuft"] = False
        except queue.Empty:
            pass
        holen_zustand()
        try:
            if not stand["zu"] and f.winfo_exists():
                stand["nach"] = f.after(150, abholen)
        except Exception:
            pass

    def arbeit(speicher, geraet, modul):
        try:
            if geraet is None:
                meldungen.put(("text", T("Verbinde mit dem Pedal …")))
                geraet = _geraet_holen(modul, einstellung_lesen("usb_geraet") or None)
                stand["geraet"] = geraet
                try:
                    einstellung_schreiben("usb_geraet", geraet.geraet)
                except Exception:
                    pass
            stand["geraet"] = geraet
            def fortschritt(fertig, gesamt, bisher):
                meldungen.put(("liste", list(bisher)))
                if gesamt:
                    meldungen.put(("anteil", (fertig / float(gesamt),
                                              T("%d von %d Patches gelesen")
                                              % (fertig, gesamt))))

            basis, patchliste = _pedal_lesen(geraet, speicher, fortschritt)
            stand["basis"], stand["patchliste"] = basis, patchliste
            meldungen.put(("liste", patchliste))
        except Exception as e:
            if not stand.get("patchliste"):
                meldungen.put(("liste", []))
            # Doppelte Meldung vermeiden: der Fehlertext enthält den Hinweis
            # oft schon selbst (21.11.2026: „… prüfen. kein JamMan gefunden …").
            text = str(e).strip()
            if text[:1].islower():
                text = text[:1].upper() + text[1:]
            hinweis = T("Kein JamMan gefunden — Pedal einschalten und Kabel prüfen.")
            if "Karte" in text or "jamman" in text.lower():
                meldungen.put(("text", text))
            else:
                meldungen.put(("text", hinweis + " " + text))
        finally:
            meldungen.put(("fertig", None))

    def hinweis_setzen():
        # Der alte Satz „Nur lesen: das Pedal wird nicht verändert." stimmt
        # seit dem 16.11.2026 nicht mehr — das Werkzeug schreibt inzwischen
        # (Audio, Patch-Ordner, Rückgängig). Jetzt steht dort, was gilt.
        if wahl.get() == "sd":
            hinweis.config(text=T("Über USB fließen etwa 1,3 MB/s. Für eine ganze "
                                  "Karte ist ein Kartenleser viel schneller. "
                                  "Schreiben verändert die Karte — vorher wird gesichert.")
                           + " " + T("Dauer und Einstellungen kommen aus Dateigröße "
                                     "und WAV-Kopf — der Ton selbst wird dafür nicht "
                                     "übertragen."))
        else:
            hinweis.config(text=T("Lesen und Schreiben über USB. Vor jedem "
                                  "Überschreiben wird der alte Platz auf den Rechner "
                                  "gesichert.")
                           + " " + T("Dauer und Einstellungen kommen aus Dateigröße "
                                     "und WAV-Kopf — der Ton selbst wird dafür nicht "
                                     "übertragen."))

    def anstossen():
        hinweis_setzen()
        if stand["laeuft"]:
            return
        stand["laeuft"] = True
        sperren(True)
        melde(T("Lädt …"))
        threading.Thread(target=arbeit, args=(wahl.get(), stand["geraet"], g_modul),
                         daemon=True).start()

    def alles_holen():
        if not stand["patchliste"]:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        mitlaufen_stoppen()
        ziel = stand.get("ziel") or ""
        if not ziel:
            ziel = ordner_waehlen(f, T("Zielordner wählen"), stand.get("ziel"))
            if not ziel:
                return
            stand["ziel"] = ziel
            stand["ziel_eigen"] = True
            try:
                einstellung_schreiben("usb_ziel", ziel)
            except Exception:
                pass
            ziel_anzeigen()
        geraetename = os.path.basename(stand["basis"].rstrip("/")) or "JamManSoloXT"
        wurzel = _hol_wurzel(ziel, geraetename, wahl.get())
        platzliste = [eintrag[0] for eintrag in stand["patchliste"]]

        def arbeit():
            import time as _zeit
            stand["abbruch"] = False
            stand["t0"] = _zeit.time()
            stand["bytes"] = 0
            geschrieben = []
            zaehler = {"gelesen": 0, "uebersprungen": 0}
            try:
                # Stufe A: nur die XML-Dateien — damit die Liste sofort erscheint
                for i, platz in enumerate(platzliste, 1):
                    if stand["abbruch"]:
                        meldungen.put(("text", T("Abgebrochen.")))
                        return
                    # Fortschritt auch in Stufe A: ohne Zähler sah es aus,
                    # als hänge der Vorgang (16.11.2026).
                    meldungen.put(("text", "%s %d/%d — %s" %
                                   (T("Hole Beschreibungen"), i, len(platzliste), platz)))
                    _patch_holen(stand["geraet"], stand["basis"] + platz,
                                 os.path.join(wurzel, platz),
                                 stopp=lambda: stand["abbruch"], nur="xml",
                                 protokoll=geschrieben, zaehler=zaehler)
                meldungen.put(("oeffnen", wurzel))
                # Stufe B: die Töne im Hintergrund
                for i, platz in enumerate(platzliste, 1):
                    if stand["abbruch"]:
                        meldungen.put(("text", T("Abgebrochen.")))
                        return
                    vergangen = max(0.1, _zeit.time() - stand["t0"])
                    tempo = (stand["bytes"] / 1048576.0) / vergangen
                    meldungen.put(("text", "%s %d/%d — %s · %.1f MB · %.2f MB/s" %
                                   (T("Hole Patch"), i, len(platzliste), platz,
                                    stand["bytes"] / 1048576.0, tempo)))
                    vorher = stand["bytes"]
                    _patch_holen(stand["geraet"], stand["basis"] + platz,
                                 os.path.join(wurzel, platz),
                                 stopp=lambda: stand["abbruch"], nur="ton",
                                 protokoll=geschrieben, zaehler=zaehler)
                    stand["bytes"] = vorher + _ordnergroesse(os.path.join(wurzel, platz))
                meldungen.put(("text", T("Fertig:") + " %d Patches → %s" % (len(platzliste), wurzel)))
                meldungen.put(("text", T("Übertragen: %d Dateien · unverändert übersprungen: %d")
                               % (zaehler["gelesen"], zaehler["uebersprungen"])))
                if stand.get("geoeffnet") == wurzel:
                    meldungen.put(("auffrischen", wurzel))
            except Exception as e:
                if "Abgebrochen" in type(e).__name__:
                    meldungen.put(("text", T("Abgebrochen.")))
                    meldungen.put(("aufraeumen", (wurzel, geschrieben)))
                else:
                    meldungen.put(("text", T("Fehler beim Lesen:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def laeuft_noch():
        """Sagt, warum ein Knopf gerade nichts tut."""
        if stand.get("laeuft"):
            melde(T("Ein Vorgang läuft noch — bitte abwarten."))
            return True
        return False

    def verbindung_neu():
        """Anschluß schließen und neu aufbauen (hilft nach einem Abbruch)."""
        if stand.get("laeuft"):
            # erst den laufenden Vorgang beenden, sonst sprechen zwei Stränge
            stand["abbruch"] = True
            melde(T("Warte auf das Ende des laufenden Vorgangs …"))
            for _ in range(50):
                if not stand.get("laeuft"):
                    break
                f.update()
                f.after(100)
                import time as _zeit
                _zeit.sleep(0.1)
            if stand.get("laeuft"):
                melde(T("Der Vorgang reagiert nicht — Fenster schließen und neu öffnen."))
                return
        _geraet_schliessen()
        stand["geraet"] = None
        stand["abbruch"] = False
        melde(T("Verbindung wird neu aufgebaut …"))
        anstossen()

    def aufs_pedal():
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        quell = ordner_waehlen(f, T("Ordner des Patches wählen"))
        if not quell:
            return
        platz = _frage_text(f, T("Aufs Pedal schreiben"),
                            T("Zielplatz auf dem Pedal (z. B. 17 oder Patch17):"),
                            "Patch50")
        platz = _platz_pruefen(f, platz)
        if not platz:
            return
        mitlaufen_stoppen()
        ziel_pfad = stand["basis"] + platz

        # Sicherheit: liegt dort schon ein Patch, wird er vorher geholt
        vorhanden = False
        try:
            _, ordner_da = stand["geraet"].ordner(stand["basis"])
            vorhanden = platz in ordner_da
        except Exception:
            pass
        if vorhanden:
            if not _frage_ja(f, 
                    T("Aufs Pedal schreiben"),
                    T("%s ist schon belegt. Der Patch wird vorher auf den Rechner "
                      "gesichert und dann überschrieben. Fortsetzen?") % platz):
                return
            platz_ordner = os.path.join(stand.get("ziel") or os.path.expanduser("~"),
                                      "vor-ueberschreiben", platz)
            sicherung = os.path.join(
                platz_ordner, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
            try:
                _patch_holen(stand["geraet"], ziel_pfad, sicherung)
                sicherung_begrenzen(platz_ordner)
            except Exception as e:
                messagebox.showerror(T("Fehler"), str(e))
                return

        def arbeit():
            try:
                # Ein belegter Platz wird geleert, nachdem die Sicherung steht:
                # sonst bleiben Dateien des alten Patches stehen, die der neue
                # nicht hat (am 16.11.2026 im Test gesehen).
                if vorhanden:
                    meldungen.put(("text", T("Leere den Platz …")))
                    try:
                        stand["geraet"].loesche(ziel_pfad, ist_ordner=True, rekursiv=True)
                    except Exception:
                        pass
                stand["geraet"].erstelle_ordner(ziel_pfad)
                meldungen.put(("text", T("Schreibe aufs Pedal …")))
                _patch_schreiben(stand["geraet"], quell, ziel_pfad,
                                 meldung=lambda name, groesse: meldungen.put(
                                     ("text", "%s %s (%.1f MB)" %
                                      (T("Schreibe:"), name, groesse / 1048576.0))),
                                 stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Fertig:") + " " + ziel_pfad))
                meldungen.put(("neu", None))
            except Exception as e:
                if "Abgebrochen" in type(e).__name__:
                    meldungen.put(("text", T("Abgebrochen.")))
                else:
                    meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def plaetze_aufruecken():
        """Lücken zwischen den belegten Plätzen schließen.

        Anlaß (17.11.2026): Nach dem Löschen von Patch16 klaffte eine Lücke
        zwischen 15 und 17. Hier rücken alle späteren Patches um eins nach
        vorn; ihre Reihenfolge bleibt, nur die Platznummern ändern sich.
        Vorher wird gefragt und jeder bewegte Platz gesichert.
        """
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        try:
            zuege = plaetze_zusammenruecken(stand["geraet"], stand["basis"])
        except Exception as e:
            _hinweis(f, T("Fehler:") + " " + str(e))
            return
        if not zuege:
            _hinweis(f, T("Es gibt keine Lücken — die Plätze sind schon "
                          "lückenlos belegt."))
            return
        speicher = T("internen Speicher") if wahl.get() == "intern" else T("der Karte")
        beispiele = ", ".join("%s → %s" % ("Patch%02d" % alt, "Patch%02d" % neu)
                              for alt, neu in zuege[:4])
        weitere = "" if len(zuege) <= 4 else " …"
        if not _frage_ja(f, T("Plätze aufrücken"),
                         T("%d Patches rücken in %s nach vorn: %s%s\n\n"
                           "Jeder bewegte Platz wird vorher auf den Rechner "
                           "gesichert. Setlists im Pedal, die auf Platznummern "
                           "zeigen, stimmen danach nicht mehr. Fortfahren?")
                         % (len(zuege), speicher, beispiele, weitere)):
            return
        mitlaufen_stoppen()
        ziel_ordner = stand.get("ziel") or os.path.expanduser("~")

        def arbeit():
            stand["abbruch"] = False
            gesichert = 0
            try:
                for i, (alt, neu) in enumerate(zuege, 1):
                    if stand["abbruch"]:
                        meldungen.put(("text", T("Abgebrochen.")))
                        return
                    alt_pfad = stand["basis"] + "Patch%02d" % alt
                    neu_pfad = stand["basis"] + "Patch%02d" % neu
                    meldungen.put(("text", "%s %d/%d — %s → %s" %
                                   (T("Rücke auf"), i, len(zuege),
                                    "Patch%02d" % alt, "Patch%02d" % neu)))
                    platz_ordner = os.path.join(ziel_ordner, "vor-ueberschreiben",
                                                "Patch%02d" % alt)
                    sicherung = os.path.join(
                        platz_ordner, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
                    try:
                        _patch_holen(stand["geraet"], alt_pfad, sicherung)
                        sicherung_begrenzen(platz_ordner)
                        gesichert += 1
                    except Exception:
                        pass
                    stand["geraet"].benenne_um(alt_pfad, neu_pfad, ist_ordner=True)
                meldungen.put(("text", T("Fertig: %d Plätze aufgerückt, %d vorher "
                                         "gesichert") % (len(zuege), gesichert)))
                ort = einstellung_lesen("usb_setlist_pfad") or ziel_ordner
                if ort and os.path.isdir(ort):
                    meldungen.put(("setlists", (ort, list(zuege))))
                meldungen.put(("hinweis", T("Aufgerückt: %d Plätze") % len(zuege)))
                meldungen.put(("neu", None))
            except Exception as e:
                meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def firmware_holen_gui():
        """Systemdateien des angeschlossenen Pedals auf den Rechner holen."""
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst \u201eNeu laden\u201c dr\u00fccken."))
            return
        start = stand.get("ziel") or os.path.expanduser("~")
        ordner = ordner_waehlen(f, T("Zielordner f\u00fcr die Firmware"), start)
        if not ordner:
            return
        mitlaufen_stoppen()

        def arbeit():
            stand["abbruch"] = False
            try:
                geholt = firmware_holen(
                    stand["geraet"], ordner,
                    melden=lambda p: meldungen.put(
                        ("text", T("Hole") + " " + str(p).split("nandfs/")[-1])),
                    stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Firmware geholt: %d Dateien") % len(geholt)))
                meldungen.put(("hinweis", T("Firmware geholt:\n%s\n%d Dateien")
                               % (ordner, len(geholt))))
            except Exception as e:
                meldungen.put(("text", T("Fehler:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def firmware_uebernehmen_gui():
        """Firmware aus einem geholten Ordner auf das Pedal schreiben."""
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst \u201eNeu laden\u201c dr\u00fccken."))
            return
        start = stand.get("ziel") or os.path.expanduser("~")
        quell = ordner_waehlen(f, T("Ordner mit der geholten Firmware"), start)
        if not quell:
            return
        vorhanden = [p for p in FIRMWARE_DATEIEN
                     if os.path.exists(os.path.join(
                         quell, p.lstrip("/").replace("/", os.sep)))]
        if not vorhanden:
            _hinweis(f, T("In diesem Ordner liegt keine Firmware "
                          "(usr/JamManUI, usr/loopengine …)."))
            return
        if not _frage_ja(f, T("Firmware \u00fcbernehmen"),
                         T("%d Firmware-Dateien werden jetzt in das Pedal "
                           "geschrieben. Vorher wird der jetzige Stand gesichert.\n\n"
                           "Ein Abbruch mittendrin kann das Pedal unbrauchbar "
                           "machen \u2014 es gibt keinen Weg zur\u00fcck.\n\n"
                           "Wirklich schreiben?") % len(vorhanden)):
            return
        sicherung = os.path.join(
            stand.get("ziel") or os.path.expanduser("~"),
            "sicherung-vor-firmware-" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
        mitlaufen_stoppen()

        def arbeit():
            stand["abbruch"] = False
            try:
                meldungen.put(("text", T("Sichere den jetzigen Stand \u2026")))
                alt = firmware_holen(stand["geraet"], sicherung,
                                     stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Gesichert: %d Dateien nach %s")
                               % (len(alt), sicherung)))
                ergebnis = firmware_schreiben(
                    stand["geraet"], quell,
                    melden=lambda p, n: meldungen.put(
                        ("text", T("Schreibe") + " " + str(p).split("nandfs/")[-1]
                         + " (%d Bytes)" % n)),
                    stopp=lambda: stand["abbruch"])
                schlecht = [p for p, _, ok in ergebnis if not ok]
                meldungen.put(("text", T("Fertig: %d Dateien geschrieben")
                               % len(ergebnis)))
                if schlecht:
                    meldungen.put(("text", T("Nicht best\u00e4tigt: %d") % len(schlecht)))
                meldungen.put(("hinweis",
                               T("Firmware geschrieben: %d Dateien\nSicherung: %s\n"
                                 "Jetzt das Pedal aus- und wieder einschalten.")
                               % (len(ergebnis), sicherung)))
            except Exception as e:
                meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def speicherabbild_aufnehmen():
        """Verzeichnisbaum des Pedals festhalten — nur lesend.

        Damit lassen sich zwei Pedale mit unterschiedlicher Firmware
        vergleichen, ohne etwas zu schreiben (17.11.2026).
        """
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        start = stand.get("ziel") or os.path.expanduser("~")
        vorgabe = "speicherabbild-%s.txt" % datetime.now().strftime("%Y-%m-%d_%H-%M")
        ziel = datei_speichern(f, T("Speicherabbild speichern"), vorgabe,
                               start, "*.txt")
        if not ziel:
            return
        mitlaufen_stoppen()

        def arbeit():
            stand["abbruch"] = False
            try:
                meldungen.put(("text", T("Lese den Verzeichnisbaum …")))
                anzahl = speicherabbild(
                    stand["geraet"], ziel,
                    melden=lambda n, p: meldungen.put(
                        ("text", "%d  %s" % (n, str(p)[:70]))),
                    stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Speicherabbild: %d Einträge") % anzahl))
                meldungen.put(("hinweis", T("Speicherabbild geschrieben:\n%s")
                               % ziel))
            except Exception as e:
                meldungen.put(("text", T("Fehler:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def speicherabbilder_vergleichen_gui():
        """Zwei aufgenommene Speicherabbilder gegenüberstellen."""
        start = stand.get("ziel") or os.path.expanduser("~")
        erst = datei_waehlen(f, T("Erstes Speicherabbild"), "*.txt", start)
        if not erst:
            return
        zweit = datei_waehlen(f, T("Zweites Speicherabbild"), "*.txt",
                              os.path.dirname(erst))
        if not zweit:
            return
        try:
            nur_a, nur_b, anders = speicherabbilder_vergleichen(erst, zweit)
        except Exception as e:
            _hinweis(f, T("Fehler:") + " " + str(e))
            return
        zeilen = [T("Erstes:") + " " + erst, T("Zweites:") + " " + zweit, "",
                  T("Nur im ersten Bild: %d") % len(nur_a)]
        zeilen += ["  + " + p for p in nur_a[:40]]
        zeilen += ["", T("Nur im zweiten Bild: %d") % len(nur_b)]
        zeilen += ["  - " + p for p in nur_b[:40]]
        zeilen += ["", T("Gleicher Pfad, andere Größe: %d") % len(anders)]
        zeilen += ["  %s  %d → %d" % (p, a_, b_) for p, a_, b_ in anders[:60]]
        if not (nur_a or nur_b or anders):
            zeilen.append(T("Die beiden Bilder sind gleich."))
        _anleitung_fenster(f, "\n".join(zeilen), T("Speicherabbilder vergleichen"))

    def platz_loeschen():
        """Einen Platz im gewählten Speicher löschen.

        Anlaß (17.11.2026): Ein versehentlich geschriebener, viel zu großer
        Patch (1238 MB) ließ sich nur durch Überschreiben loswerden — und
        davor sicherte das Werkzeug ihn erst einmal über USB. Löschen ist der
        gerade Weg.
        """
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        platz = _frage_text(f, T("Platz löschen"),
                            T("Welcher Platz soll gelöscht werden? "
                              "(z. B. 30 oder Patch30)"), "")
        platz = _platz_pruefen(f, platz)
        if not platz:
            return
        ziel_pfad = stand["basis"] + platz
        try:
            _, vorhandene = stand["geraet"].ordner(stand["basis"])
        except Exception as e:
            melde(T("Fehler:") + " " + str(e))
            return
        if platz not in vorhandene:
            _hinweis(f, T("%s ist nicht belegt — nichts zu löschen.") % platz)
            return
        speicher = T("internen Speicher") if wahl.get() == "intern" else T("der Karte")
        if not _frage_ja(f, T("Platz löschen"),
                         T("%s wird aus %s gelöscht. Das läßt sich nicht "
                           "rückgängig machen. Fortfahren?") % (platz, speicher)):
            return
        mitlaufen_stoppen()

        def arbeit():
            stand["abbruch"] = False
            try:
                meldungen.put(("text", T("Lösche") + " " + platz + " …"))
                stand["geraet"].loesche(ziel_pfad, ist_ordner=True, rekursiv=True)
                meldungen.put(("text", T("Gelöscht:") + " " + platz))
                meldungen.put(("hinweis", T("Gelöscht: %s") % platz))
                meldungen.put(("neu", None))
            except Exception as e:
                meldungen.put(("text", T("Fehler beim Löschen:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def ordner_aufs_pedal():
        """Alle Patch-Ordner eines Ordners in den gewählten Speicher schreiben.

        Anlaß (17.11.2026): Eine geholte Kopie ließ sich nur Patch für Patch
        zurückspielen. Jetzt: Ordner wählen, alle Patch01 … PatchNN werden
        geschrieben — belegte Plätze vorher gesichert (fünf Stände je Platz),
        unveränderte übersprungen.
        """
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        quell = ordner_waehlen(f, T("Ordner mit Patch-Ordnern wählen"))
        if not quell:
            return
        muster = re.compile(r"^Patch(\d{1,3})$", re.IGNORECASE)
        patches = []
        for name in sorted(os.listdir(quell)):
            treffer = muster.match(name)
            if treffer and os.path.isdir(os.path.join(quell, name)):
                patches.append((name, "Patch%02d" % int(treffer.group(1))))
        if not patches:
            _hinweis(f, T("Im Ordner liegen keine Patch-Ordner "
                          "(erwartet: Patch01 … Patch200)."))
            return
        speicher = T("internen Speicher") if wahl.get() == "intern" else T("der Karte")
        if not _frage_ja(f, T("Ganzen Ordner aufs Pedal"),
                         T("%d Patch-Ordner werden in %s geschrieben. Belegte Plätze "
                           "werden vorher gesichert, unveränderte übersprungen. "
                           "Fortfahren?") % (len(patches), speicher)):
            return
        mitlaufen_stoppen()
        ziel_ordner = stand.get("ziel") or os.path.expanduser("~")

        def arbeit():
            stand["abbruch"] = False
            try:
                geschrieben, uebersprungen, gesichert = patches_aufs_pedal(
                    stand["geraet"], stand["basis"], quell, ziel_ordner,
                    melden=lambda text: meldungen.put(("text", text)),
                    stopp=lambda: stand["abbruch"])
                meldungen.put(("text",
                               T("Fertig: %d Patches geschrieben, %d übersprungen, "
                                 "%d vorher gesichert")
                               % (geschrieben, uebersprungen, gesichert)))
            except Exception as e:
                if "Abgebrochen" in type(e).__name__:
                    meldungen.put(("text", T("Abgebrochen.")))
                else:
                    meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def als_zip():
        if laeuft_noch():
            return
        mitlaufen_stoppen()
        if not stand["patchliste"] or not stand.get("ziel"):
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        name = (T("JamMan") + "-" +
                (T("intern") if wahl.get() == "intern" else T("Karte")))
        ziel_datei = datei_speichern(f, T("Als ZIP sichern"),
                                     name + ".zip", stand["ziel"], "*.zip")
        if not ziel_datei:
            return
        platzliste = [p for p, _, _ in stand["patchliste"]]

        def arbeit():
            try:
                meldungen.put(("text", T("Sichere als ZIP …")))
                gesamt = max(1, len(platzliste))
                _als_zip(stand["geraet"], stand["basis"], platzliste, ziel_datei,
                         meldung=lambda name, i: meldungen.put(
                             ("anteil", ((i - 0.5) / gesamt,
                                         "%s %s" % (T("Sichere:"), name)))),
                         stopp=lambda: stand["abbruch"])
                meldungen.put(("anteil", (1.0, T("fertig"))))
                meldungen.put(("text", T("Fertig:") + " " + ziel_datei))
            except Exception as e:
                if os.path.exists(ziel_datei + ".part"):
                    try:
                        os.remove(ziel_datei + ".part")
                    except OSError:
                        pass
                # Ein Abbruch ist kein Fehler (19.11.2026: Nach dem Schließen
                # während des Sicherns stand „Fehler beim Sichern:" da).
                if "Abgebrochen" in type(e).__name__ or "Abgebrochen" in str(e):
                    meldungen.put(("text", T("Abgebrochen — keine Datei geschrieben.")))
                    meldungen.put(("verbindung_neu", None))
                else:
                    meldungen.put(("text", T("Fehler beim Sichern:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("balken_zu", None))
                meldungen.put(("fertig", None))
        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def alle_auswaehlen(*_a):
        liste.selection_set(0, "end")
        return "break"

    def setlist_speichern():
        if not stand["patchliste"]:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        indizes = [i for i in range(len(stand["patchliste"]))
                   if liste.selection_includes(i)]
        if not indizes:
            # Ohne Auswahl alle Patches nehmen — das ist der häufigste Fall
            if not _frage_ja(f, 
                    T("Setlist sichern"),
                    T("Nichts markiert. Alle %d Patches in die Setlist?"
                      % len(stand["patchliste"]))):
                return
            indizes = list(range(len(stand["patchliste"])))
        start = einstellung_lesen("usb_setlist_pfad") or stand.get("ziel") or None
        ziel_datei = datei_speichern(f, T("Setlist sichern"),
                                     T("Setlist.txt"), start, "*.txt")
        if not ziel_datei:
            return
        einstellung_schreiben("usb_setlist_pfad", os.path.dirname(ziel_datei))
        geraet = stand.get("geraet")
        geraetename = geraet.kopf.get("name", "") if geraet is not None else ""
        _setlist_speichern(stand["patchliste"], indizes, ziel_datei, geraetename)
        melde(T("Fertig:") + " " + ziel_datei)

    def jamlist_holen():
        """JamList aus der Bibliothek von JamManager XT als Textdatei holen."""
        start = einstellung_lesen("usb_setlist_pfad") or os.path.expanduser("~")
        db = datei_waehlen(f, T("Bibliothek von JamManager XT"), "*.sqlite *.db", start)
        if not db:
            return
        try:
            listen = jamlists_lesen(db)
        except Exception as e:
            _hinweis(f, T("Fehler:") + " " + str(e))
            return
        if not listen:
            _hinweis(f, T("In dieser Bibliothek liegt keine JamList."))
            return
        namen = ["%s (%d)" % (l["name"] or "?", len(l["platz"])) for l in listen]
        index = _frage_auswahl(f, T("JamList w\u00e4hlen"), namen)
        if index is None:
            return
        liste = listen[index]
        ziel = datei_speichern(f, T("Setlist speichern"),
                               (liste["name"] or "JamList") + ".txt",
                               os.path.dirname(db), "*.txt")
        if not ziel:
            return
        eintraege = [(("Patch%02d" % nr), n, "") for nr, n, _ in liste["platz"]]
        _setlist_speichern(eintraege, list(range(len(eintraege))), ziel)
        einstellung_schreiben("usb_setlist_pfad", os.path.dirname(ziel))
        _hinweis(f, T("Geholt: %s\n%d Pl\u00e4tze\n%s")
                 % (liste["name"], len(eintraege), ziel))

    def jamlist_ablegen():
        """Eine Textsetlist als JamList in die Bibliothek eintragen."""
        start = einstellung_lesen("usb_setlist_pfad") or os.path.expanduser("~")
        db = datei_waehlen(f, T("Bibliothek von JamManager XT"), "*.sqlite *.db", start)
        if not db:
            return
        quelle = datei_waehlen(f, T("Setlist laden"), "*.txt", os.path.dirname(db))
        if not quelle:
            return
        name = os.path.basename(quelle).rsplit(".", 1)[0]
        if not _frage_ja(f, T("JamList ablegen"),
                         T("%s%s%s als neue JamList eintragen? Die Bibliothek "
                           "wird dabei ver\u00e4ndert \u2014 vorher eine Kopie anlegen.")
                         % ("\u201e", name, "\u201c")):
            return
        try:
            eintraege = []
            for platz, patch_name in _setlist_lesen(quelle):
                zahl = int(str(platz).replace("Patch", "") or 0)
                eintraege.append((zahl, patch_name, ""))
            kennung = jamlist_schreiben(db, name, eintraege)
        except Exception as e:
            _hinweis(f, T("Fehler:") + " " + str(e))
            return
        _hinweis(f, T("JamList %s angelegt (id %d, %d Pl\u00e4tze)")
                 % (name, kennung, len(eintraege)))

    def setlist_holen():
        mitlaufen_stoppen()
        if stand["geraet"] is None or not stand.get("ziel"):
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        start = einstellung_lesen("usb_setlist_pfad") or stand.get("ziel") or None
        quelle = datei_waehlen(f, T("Setlist laden"), "*.txt", start)
        if not quelle:
            return
        einstellung_schreiben("usb_setlist_pfad", os.path.dirname(quelle))
        try:
            eintraege = _setlist_lesen(quelle)
        except Exception as e:
            messagebox.showerror(T("Fehler"), str(e))
            return
        if not eintraege:
            messagebox.showinfo(T("Hinweis"), T("Die Setlist ist leer."))
            return
        platzliste = [p for p, _ in eintraege]
        wurzel = _hol_wurzel(stand["ziel"],
                             os.path.basename(stand["basis"].rstrip("/")),
                             wahl.get())

        def arbeit():
            try:
                for i, platz in enumerate(platzliste, 1):
                    if stand["abbruch"]:
                        meldungen.put(("text", T("Abgebrochen.")))
                        return
                    meldungen.put(("text", "%s %d/%d — %s" %
                                   (T("Hole Patch"), i, len(platzliste), platz)))
                    _patch_holen(stand["geraet"], stand["basis"] + platz,
                                 os.path.join(wurzel, platz),
                                 stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Fertig:") + " " + wurzel))
                try:
                    einstellung_schreiben("usb_letzter_holpfad", wurzel)
                except Exception:
                    pass
                meldungen.put(("oeffnen", wurzel))
            except Exception as e:
                meldungen.put(("text", T("Fehler beim Lesen:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))
        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def rueckgaengig():
        if laeuft_noch():
            return
        sicherung = einstellung_lesen("usb_letzte_sicherung")
        platz = einstellung_lesen("usb_letzter_platz")
        speicher = einstellung_lesen("usb_letzter_speicher")
        if not (sicherung and platz and os.path.isdir(sicherung)):
            messagebox.showinfo(T("Hinweis"), T("Es gibt nichts rückgängig zu machen."))
            return
        if speicher and speicher != wahl.get():
            # Die Sicherung stammt aus dem anderen Speicher. Früher wurde sie
            # stillschweigend dorthin geschrieben, wo gerade die Auswahl stand
            # (19.11.2026 von ruff gefunden: F841 — gelesen, nie benutzt).
            messagebox.showinfo(T("Hinweis"), T(
                "Die letzte Sicherung stammt aus dem anderen Speicher. "
                "Bitte oben umschalten und es noch einmal versuchen."))
            return
        if not _frage_ja(f, 
                T("Rückgängig"),
                T("%s wird durch die Sicherung ersetzt. Fortsetzen?") % platz):
            return

        def arbeit():
            try:
                ziel_pfad = stand["basis"] + platz
                meldungen.put(("text", T("Stelle wieder her:") + " " + platz))
                try:
                    stand["geraet"].loesche(ziel_pfad, ist_ordner=True, rekursiv=True)
                except Exception:
                    pass
                stand["geraet"].erstelle_ordner(ziel_pfad)
                _patch_schreiben(stand["geraet"], sicherung, ziel_pfad,
                                 stopp=lambda: stand["abbruch"])
                meldungen.put(("text", T("Fertig:") + " " + ziel_pfad))
                meldungen.put(("neu", None))
            except Exception as e:
                meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                meldungen.put(("fertig", None))
        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def audio_aufs_pedal():
        if laeuft_noch():
            return
        if stand["geraet"] is None:
            _hinweis(f, T("Erst „Neu laden“ drücken."))
            return
        start = einstellung_lesen("usb_audio_pfad") or stand.get("ziel") or None
        quelle = datei_waehlen(f, T("Audiodatei wählen"), "*.wav *.aif *.aiff *.aifc *.caf *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma", start)
        einstellung_schreiben("usb_audio_pfad", os.path.dirname(quelle))
        if not quelle:
            return
        platz = _frage_text(f, T("Audio aufs Pedal"),
                            T("Zielplatz auf dem Pedal (z. B. 17 oder Patch17):"),
                            "Patch50")
        platz = _platz_pruefen(f, platz)
        if not platz:
            return
        mitlaufen_stoppen()
        ziel_pfad = stand["basis"] + platz
        name = os.path.splitext(os.path.basename(quelle))[0]
        geraetename = os.path.basename(stand["basis"].rstrip("/")) or "JamManSoloXT"

        vorhanden = False
        try:
            _, ordner_da = stand["geraet"].ordner(stand["basis"])
            vorhanden = platz in ordner_da
        except Exception:
            pass
        if vorhanden and not _frage_ja(f, 
                T("Audio aufs Pedal"),
                T("%s ist schon belegt. Der Patch wird vorher auf den Rechner "
                  "gesichert und dann überschrieben. Fortsetzen?") % platz):
            return

        def arbeit():
            import tempfile
            try:
                if vorhanden:
                    platz_ordner = os.path.join(
                        stand.get("ziel") or os.path.expanduser("~"),
                        "vor-ueberschreiben", platz)
                    sicherung = os.path.join(
                        platz_ordner, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
                    meldungen.put(("text", T("Sichere den alten Platz …")))
                    _patch_holen(stand["geraet"], ziel_pfad, sicherung)
                    sicherung_begrenzen(platz_ordner)
                    einstellung_schreiben("usb_letzte_sicherung", sicherung)
                    einstellung_schreiben("usb_letzter_platz", platz)
                    einstellung_schreiben("usb_letzter_speicher", wahl.get())
                # Länge prüfen, BEVOR Minuten über USB geschrieben werden:
                # das Pedal nimmt höchstens 10 Minuten je Loop an (Handbuch).
                # Am 17.11.2026 lief eine 1238-MB-Datei (~117 Minuten) los.
                try:
                    dauer_quelle = wav_info(quelle)[0]
                except Exception:
                    dauer_quelle = 0.0
                if dauer_quelle > MAX_LOOP_SEKUNDEN:
                    meldungen.put(("text", T("Die Datei ist %s lang — das Pedal nimmt "
                                             "höchstens 10 Minuten je Loop an. Bitte im "
                                             "Hauptfenster kürzen oder teilen.")
                                   % sekunden(dauer_quelle)))
                    meldungen.put(("fertig", None))
                    return
                meldungen.put(("text", T("Wandle die Datei …")))
                temp = tempfile.mkdtemp()
                wav = os.path.join(temp, "phrase.wav")
                if not wandle_nach_44k(quelle, wav):
                    if ffmpeg_fehlt():
                        # Der eigentliche Grund: ffmpeg fehlt (17.11.2026
                        # meldete das Fenster nur „Umwandeln fehlgeschlagen").
                        meldungen.put(("text", T("Zum Umwandeln fehlt ffmpeg.")))
                        meldungen.put(("anleitung", ffmpeg_anleitung()))
                    else:
                        meldungen.put(("text", T("Umwandeln fehlgeschlagen — die "
                                                 "Datei muß 44,1 kHz, 16 Bit, "
                                                 "Stereo werden.")))
                    return
                try:
                    dauer_fertig = wav_info(wav)[0]
                except Exception:
                    dauer_fertig = 0.0
                if dauer_fertig > MAX_LOOP_SEKUNDEN:
                    meldungen.put(("text", T("Die Datei ist %s lang — das Pedal nimmt "
                                             "höchstens 10 Minuten je Loop an. Bitte im "
                                             "Hauptfenster kürzen oder teilen.")
                                   % sekunden(dauer_fertig)))
                    meldungen.put(("fertig", None))
                    return
                meldungen.put(("text", T("Schreibe aufs Pedal …")))
                stand["geraet"].loesche(ziel_pfad, ist_ordner=True, rekursiv=True) if vorhanden else None
                stand["geraet"].erstelle_ordner(ziel_pfad)
                stand["geraet"].erstelle_ordner(ziel_pfad + "/PhraseA")
                stand["geraet"].schreibe_datei(ziel_pfad + "/patch.xml",
                                               _neuer_patch_xml(geraetename, name),
                                               stopp=lambda: stand["abbruch"])
                stand["geraet"].schreibe_datei(ziel_pfad + "/PhraseA/phrase.xml",
                                               _neue_phrase_xml(),
                                               stopp=lambda: stand["abbruch"])
                with open(wav, "rb") as fp:
                    daten = fp.read()
                stand["geraet"].schreibe_datei(ziel_pfad + "/PhraseA/phrase.wav", daten,
                                               stopp=lambda: stand["abbruch"],
                                               fortschritt=lambda f, g: meldungen.put(
                                                   ("text", "%s %.1f / %.1f MB" %
                                                    (T("Schreibe:"), f / 1048576.0,
                                                     g / 1048576.0))))
                meldungen.put(("text", T("Fertig:") + " " + platz + " — " + name))
                meldungen.put(("neu", None))
            except Exception as e:
                if "Abgebrochen" in type(e).__name__:
                    meldungen.put(("text", T("Abgebrochen.")))
                else:
                    meldungen.put(("text", T("Fehler beim Schreiben:") + " " + str(e)))
            finally:
                stand["abbruch"] = False
                meldungen.put(("fertig", None))

        stand["laeuft"] = True
        sperren(True)
        threading.Thread(target=arbeit, daemon=True).start()

    def mitlaufen_stoppen():
        """Mitlaufen beenden, bevor gelesen oder geschrieben wird.

        Zwei Stränge am selben Anschluß brachten das Pedal am 16.11.2026 zum
        Schweigen: der Mitlauf-Strang nahm Antworten weg, die zum Schreiben
        gehörten.
        """
        stand["mitlaufen"] = False

    # Was während eines Laufs gesperrt wird: alle Knöpfe und Auswahlmenüs
    def schliessen():
        # Läuft noch eine Arbeit, erst fragen (19.11.2026: Das Fenster ließ
        # sich mitten im ZIP-Sichern schließen — der Vorgang lief unsichtbar
        # weiter, und eine halbe Datei blieb liegen).
        if stand.get("laeuft"):
            if not _frage_ja(
                    f, T("Pedal über USB"),
                    T("Es läuft noch eine Arbeit. Abbrechen und das Fenster "
                      "schließen?\n\nDie unfertige Datei wird gelöscht.")):
                try:
                    f.lift()
                except Exception:
                    pass
                return
            stand["abbruch"] = True
            # Kurz warten, damit der Arbeitsfaden die Datei aufräumen kann.
            import time as _zeit
            ende = _zeit.time() + 5.0
            while stand.get("laeuft") and _zeit.time() < ende:
                try:
                    f.update()
                except Exception:
                    break
                _zeit.sleep(0.05)
        # erst den Zeitgeber abbestellen, sonst meldet Tk nach dem Schließen
        # „invalid command name …abholen" (16.11.2026)
        balken_zu()
        stand["zu"] = True
        try:
            if stand.get("nach"):
                f.after_cancel(stand["nach"])
        except Exception:
            pass
        _geraet_schliessen()
        stand["geraet"] = None
        _USB_FENSTER["fenster"] = None
        f.destroy()

    def abbrechen():
        if stand["laeuft"]:
            stand["abbruch"] = True
            melde(T("Wird abgebrochen …"))

    # Bedienung: wenige Knöpfe, der Rest in aufklappbaren Menüs. Am 16.11.2026
    # waren es 15 Knöpfe in drei Reihen — technisch in Ordnung, aber niemand
    # findet dort etwas.
    reihe1 = tk.Frame(f, bg=FARBE_HINTERGRUND)
    reihe1.pack(fill="x", padx=14, pady=(0, 4))
    tk.Button(reihe1, text=T("Neu laden"), command=anstossen).pack(side="left")
    knopf_holen = tk.Button(reihe1, text=T("Ins Hauptfenster holen …"),
                            command=alles_holen)
    knopf_holen.pack(side="left", padx=8)

    def holen_zustand():
        """Holen geht erst, wenn wirklich ein Pedal antwortet (21.11.2026).

        Vorher war der Knopf immer bedienbar und antwortete nur mit einem
        Hinweis; jetzt sieht man am Knopf, ob eine Verbindung steht.
        """
        bereit = stand.get("geraet") is not None and not stand.get("laeuft")
        try:
            knopf_holen.config(state="normal" if bereit else "disabled")
        except Exception:
            pass
    tk.Button(reihe1, text=T("Schließen"), command=schliessen).pack(side="right")

    def geschuetzt(befehl):
        """Einen Befehl so aufrufen, daß ein Fehler als Meldung erscheint.

        Am 16.11.2026 blieb „Audio in den Speicher schreiben“ wirkungslos:
        im Menübefehl fehlte ein Import, die Ausnahme landete nur im
        Terminal, im Fenster passierte nichts.
        """
        def huelle(*_a, **_k):
            try:
                return befehl(*_a, **_k)
            except Exception as e:
                messagebox.showerror(T("Fehler"), "%s: %s" % (type(e).__name__, e))
        return huelle

    def menue(eltern, beschriftung, eintraege):
        mb = tk.Menubutton(eltern, text=beschriftung + "  ▾", anchor="w",
                           bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                           activebackground=FARBE_AUSWAHL, activeforeground="#ffffff",
                           relief="raised", bd=1, padx=8, pady=2)
        m = tk.Menu(mb, tearoff=0, bg=FARBE_LISTE, fg=FARBE_SCHRIFT,
                    activebackground=FARBE_AUSWAHL, activeforeground="#ffffff")
        for text, befehl in eintraege:
            m.add_command(label=text, command=geschuetzt(befehl))
        mb["menu"] = m
        mb.pack(side="left", padx=(0, 6))
        return mb

    reihe2 = tk.Frame(f, bg=FARBE_HINTERGRUND)
    reihe2.pack(fill="x", padx=14, pady=(0, 6))
    menue(reihe2, T("Sichern"), [
        (T("Als ZIP sichern …"), als_zip),
        (T("Firmware dieses Pedals holen …"), firmware_holen_gui),
        (T("Speicherabbild aufnehmen …"), speicherabbild_aufnehmen),
        (T("Speicherabbilder vergleichen …"), speicherabbilder_vergleichen_gui),
        (T("Sicherungen aufräumen …"),
         lambda: fenster_aufraeumen(stand.get("ziel") or "", f,
                                   karte=stand.get("basis"))),
    ])
    menue(reihe2, T("Schreiben"), [
        (T("Patch-Ordner aufs Pedal …"), aufs_pedal),
        (T("Ganzen Ordner aufs Pedal …"), ordner_aufs_pedal),
        (T("Audio in den Speicher schreiben …"), audio_aufs_pedal),
        (T("Rückgängig"), rueckgaengig),
        (T("Firmware übernehmen …"), firmware_uebernehmen_gui),
        (T("Platz löschen …"), platz_loeschen),
        (T("Plätze aufrücken …"), plaetze_aufruecken),
    ])
    menue(reihe2, T("Setlists"), [
        (T("Alle auswählen"), alle_auswaehlen),
        (T("JamList aus JamManager XT holen …"), jamlist_holen),
        (T("Setlist als JamList ablegen …"), jamlist_ablegen),
        (T("Setlist sichern …"), setlist_speichern),
        (T("Setlist holen …"), setlist_holen),
    ])
    tk.Button(reihe2, text=T("Verbindung neu aufbauen"),
              command=verbindung_neu).pack(side="left", padx=(0, 6))
    tk.Button(reihe2, text=T("Abbrechen"), command=abbrechen).pack(side="right")

    # Zielordner dauerhaft anzeigen: nach dem ersten Wählen stand er nur
    # kurz in der Kopfzeile und war danach weg (16.11.2026).
    ziel_zeile = tk.Frame(f, bg=FARBE_HINTERGRUND)
    ziel_zeile.pack(fill="x", padx=14, pady=(0, 10))
    ziel_label = tk.Label(ziel_zeile, text="", bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
                          font=schrift(9), anchor="w", justify="left", wraplength=430)
    ziel_label.pack(side="left", fill="x", expand=True)

    # Vorschlag für den Zielordner — auf allen drei Systemen:
    # macOS und Linux haben ~/Documents, Windows meist auch; fehlt der
    # Ordner (oder ist er verschoben), wird der Benutzerordner genommen.
    _dokumente = os.path.expanduser("~/Documents")
    if not os.path.isdir(_dokumente):
        _dokumente = os.path.expanduser("~")
    VORSCHLAG = os.path.join(_dokumente, "JamMan-Sicherung")

    def ziel_anzeigen():
        if not stand["ziel"]:
            gespeichert = einstellung_lesen("usb_ziel")
            if gespeichert:
                stand["ziel"] = gespeichert
                stand["ziel_eigen"] = True
            else:
                stand["ziel"] = VORSCHLAG      # Vorschlag, noch nicht bestätigt
                stand["ziel_eigen"] = False
        text = T("Ziel:") + " " + stand["ziel"]
        if not stand["ziel_eigen"]:
            text += "   " + T("(Vorschlag)")
        ziel_label.config(text=text)

    def ziel_waehlen():
        p = ordner_waehlen(f, T("Zielordner wählen"),
                               stand["ziel"] or None)
        if p:
            stand["ziel"] = p
            stand["ziel_eigen"] = True
            try:
                einstellung_schreiben("usb_ziel", p)
            except Exception:
                pass
            ziel_anzeigen()

    tk.Button(ziel_zeile, text=T("Ziel ändern …"),
              command=ziel_waehlen).pack(side="right")
    ziel_anzeigen()

    f.protocol("WM_DELETE_WINDOW", schliessen)

    # Die Knopfreihen sind mit der Zeit mehr geworden; bei fester Höhe fiel
    # die unterste Reihe aus dem Fenster (16.11.2026). Deshalb wird die Höhe
    # nach dem Aufbau am tatsächlichen Bedarf ausgerichtet.
    try:
        f.update_idletasks()
        breite = max(760, f.winfo_reqwidth())
        hoehe = f.winfo_reqheight()
        bildschirm = f.winfo_screenheight()
        hoehe = min(max(hoehe, 520), bildschirm - 120)
        f.geometry("%dx%d" % (breite, hoehe))
    except Exception:
        pass

    # Erst hier einsammeln — jetzt sind alle Knöpfe und Menüs angelegt
    # (am 17.11.2026 stand die Stelle zu früh, deshalb blieben „Neu laden“,
    #  „Ins Hauptfenster holen“ und „Verbindung neu aufbauen“ bedienbar).
    sperrbar.extend(sperrbare_widgets(f, ausnahmen=(T("Abbrechen"), T("Schließen"))))

    f.after(150, abholen)
    anstossen()


def ist_kopie(pfad):
    """Ist das ein geholter Ordner vom Pedal (also nur auf dem Rechner)?"""
    pfad = (pfad or "").replace("\\", "/")
    return "/intern/" in pfad or "/karte/" in pfad


def _fenster_titel(basis, unterordner=""):
    """Überschrift des Hauptfensters passend zu dem, was offen ist.

    Anlaß (16.11.2026): „Speicherkarte des JamMan Solo XT" stand auch dann
    noch da, als eine geholte Kopie oder der interne Speicher offen war.
    """
    pfad = (basis or "").replace("\\", "/")
    if not pfad:
        return T("keine Karte")
    if "/intern/" in pfad:
        return T("Interner Speicher") + " — " + T("Kopie vom Pedal")
    if "/karte/" in pfad:
        return T("Speicherkarte") + " — " + T("Kopie vom Pedal")
    if "stereo" in (unterordner or "").lower():
        return T("Speicherkarte des JamMan Stereo")
    if unterordner and unterordner.lower() not in ("jamman", "jamman solixt"):
        # Unbekanntes Gerät: den Ordnernamen zeigen, nicht „Solo XT" behaupten
        # (20.11.2026 — die Überschrift behauptete sonst ein Gerät, das es nicht
        # ist).
        if not unterordner.lower().startswith("jammansoloxt"):
            return T("Speicherkarte des %s") % unterordner
    return T("Speicherkarte des JamMan Solo XT")


def gui(karte):
    """Oberfläche. Der Ton läuft nebenher: kein Warten, Stopp jederzeit."""
    # Drei Zeilen im Terminal, damit man sieht, daß es läuft und was es sieht
    # (18.11.2026: beim Start aus dem Terminal blieb es sonst völlig still).
    print("  " + T("JamMan Card Tool") + " " + FASSUNG)
    try:
        # Nicht den übergebenen Wert zeigen, sondern das, was wirklich gefunden
        # wurde — am 18.11.2026 stand dort "/media" statt „keine Karte".
        basis = karte_finden(karte, streng=False)
        if basis and os.path.isdir(basis):
            print("  " + T("Karte:") + " " + basis)
        else:
            print("  " + T("Karte:") + " " + T("keine Karte gefunden"))
        print("  " + T("Einstellungen:") + " " + EINSTELLUNG_DATEI)
    except Exception:
        pass
    print("  " + T("Meldungen stehen im Fenster; Fehler zusätzlich in "
                   "jamman-fehler.log."))
    print()
    # Sicherung beim Start - vor dem Fenster, damit man den Fortschritt
    # im Terminal sieht und das Fenster nicht einfriert.
    if einstellung_lesen("start_sichern") == "ja":
        beim_start_sichern(karte or karte_finden())

    import tkinter as tk
    from tkinter import messagebox as _mb
    from tkinter import simpledialog
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

    basis = karte_finden(karte, streng=False)

    global GUI_FENSTER
    fenster = tk.Tk()
    GUI_FENSTER = fenster
    fenster.minsize(1000, 720)
    fenster.configure(bg=FARBE_HINTERGRUND)
    # Größe und Lage merken (19.11.2026): Wer das Fenster auf seinen Bildschirm
    # zieht, soll das nicht bei jedem Start wieder tun.
    _geo = einstellung_lesen("fenster_geometrie")
    if _geo and "x" in _geo:
        try:
            fenster.geometry(_geo)
        except Exception:
            pass

    # Ohne Karte: kein Dialog mehr. Früher fragte eine Meldung „Ordner selbst
    # wählen?" — auf Linux war das der erste Bildschirm, obwohl das Pedal per
    # USB erreichbar war (Rückmeldung 17.11.2026). Jetzt erscheint das Fenster
    # mit dem Hinweis, und wenn ein Pedal am Kabel hängt, öffnet sich gleich
    # das USB-Fenster mit Karte und internem Speicher.
    if basis is None:
        fenster.deiconify()

    # Einmal am Tag bei GitHub nachsehen, ob es eine neuere Fassung gibt.
    # Läuft im Hintergrund: ohne Netz startet das Fenster trotzdem sofort.
    def jetzt_aktualisieren():
        """Holt die neueste Fassung im Hintergrund und meldet das Ergebnis.

        Der Download läuft in einem Nebenlauf, damit das Fenster nicht
        einfriert; der Hauptlauf fragt alle 300 ms nach (tkinter darf nur
        aus dem Hauptlauf angesprochen werden).
        """
        weiter, autom = frage_ja_nein_mit_haken(
            fenster, T("Aktualisieren"),
            T("Die neueste Fassung wird von GitHub geladen und ersetzt die "
              "Programmdateien. Ihre Einstellungen und Ihre Karte bleiben "
              "unberührt. Fortfahren?"),
            T("beim Start automatisch nach Updates sehen"),
            einstellung_lesen("update_auto") != "nein")
        try:
            einstellung_schreiben("update_auto", "ja" if autom else "nein")
        except Exception:
            pass
        if not weiter:
            return
        stand = {"fertig": False, "ok": False, "text": ""}

        def lauf():
            try:
                ok, text = aktualisieren()
            except Exception as fehler:      # nie ohne Meldung enden
                ok, text = False, T("Unerwarteter Fehler: %s") % fehler
            stand["ok"] = ok
            stand["text"] = text
            stand["fertig"] = True

        def nachsehen():
            if not stand["fertig"]:
                fenster.after(300, nachsehen)
                return
            if not stand["ok"]:
                messagebox.showinfo(T("Nicht aktualisiert"), stand["text"])
                return
            if frage_ja_nein(fenster, T("Aktualisiert"),
                                   stand["text"] + "\n\n"
                                   + T("Fenster jetzt schließen?")):
                fenster.destroy()

        threading.Thread(target=lauf, daemon=True).start()
        fenster.after(300, nachsehen)

    def _status(text):
        """Statuszeile setzen — oder, falls es sie noch nicht gibt, die Konsole."""
        try:
            status_var.set(text)
        except Exception:
            print("  " + str(text))

    def fassung_melden(tag):
        """Meldet eine neuere Fassung — und installiert sie, wenn gewünscht.

        „Automatisch" heißt: ohne Rückfrage holen und einsetzen, danach einmal
        sagen, daß ein Neustart nötig ist. Abschaltbar über die Einstellung
        update_auto = nein (Häkchen „Update beim Start").
        """
        try:
            if not tag or _numerisch(tag) <= _numerisch(eigene_fassung()):
                return
            automatisch = einstellung_lesen("update_auto") != "nein"
            if not automatisch:
                if frage_ja_nein(
                        fenster, T("Neue Fassung verfügbar"),
                        T("Es gibt eine neuere Fassung: %s. Sie benutzen %s.")
                        % (tag, eigene_fassung()) + "\n\n"
                        + T("Jetzt aktualisieren?")):
                    jetzt_aktualisieren()
                return
            _status(T("Neue Fassung %s wird geholt …") % tag)
            stand = {"fertig": False, "ok": False, "text": ""}

            def lauf():
                try:
                    stand["ok"], stand["text"] = aktualisieren()
                except Exception as f:
                    stand["ok"], stand["text"] = False, str(f)
                stand["fertig"] = True

            def nachsehen():
                if not stand["fertig"]:
                    fenster.after(300, nachsehen)
                    return
                _status(stand["text"])
                if stand["ok"]:
                    messagebox.showinfo(
                        T("Aktualisiert"),
                        T("Fassung %s ist installiert. Bitte das Fenster "
                          "schließen und neu starten.") % tag)
            threading.Thread(target=lauf, daemon=True).start()
            fenster.after(300, nachsehen)
        except Exception:
            pass

    def fassung_im_hintergrund():
        """Fragt im Hintergrund bei GitHub nach; das Fenster wartet nicht.

        Der Fund wird NICHT aus dem Nebenlauf ans Fenster gemeldet -
        tkinter darf nur aus dem Hauptlauf angesprochen werden. Der
        Hauptlauf fragt stattdessen alle 300 ms nach.
        """
        if einstellung_lesen("update_pruefung") == "nein":
            return
        heute = datetime.now().strftime("%Y-%m-%d")
        if einstellung_lesen("update_geprueft") == heute:
            return

        stand = {"fertig": False, "tag": None}

        def lauf():
            tag = fassung_pruefen()
            try:
                einstellung_schreiben("update_geprueft", heute)
            except Exception:
                pass
            stand["tag"] = tag
            stand["fertig"] = True

        def nachsehen():
            if not stand["fertig"]:
                fenster.after(300, nachsehen)
                return
            if stand["tag"]:
                fassung_melden(stand["tag"])

        threading.Thread(target=lauf, daemon=True).start()
        fenster.after(300, nachsehen)

    fassung_im_hintergrund()

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

    # Eigener Knopf zum Beenden, ganz rechts hinter der Sprachwahl
    # (18.11.2026). Beendet das Fenster sauber; das Startskript schließt
    # danach auch das Terminal. Zuerst gepackt heißt bei side="right":
    # am weitesten rechts.
    def beenden():
        try:
            fenster.destroy()
        except Exception:
            pass

    tk.Button(leiste, text=T("Beenden"), command=beenden).pack(side="right",
                                                              padx=(12, 0))

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
    def usb_oeffnen():
        def oeffne(pfad):
            zustand["basis"] = karte_pruefen(pfad)
            neu_laden()
        usb_fenster(fenster, oeffne_karte=oeffne)

    # Selbsttest: prüft das Werkzeug auf DIESEM Rechner, ohne Pedal
    # (19.11.2026 — der Nutzer fragte, wo er ihn findet; ein Knopf ist
    # leichter zu finden als ein Aufruf mit Argument).
    def selbsttest_starten():
        weiter, autom = frage_ja_nein_mit_haken(
            fenster, T("Selbsttest"),
            T("Das Werkzeug prüft sich selbst: es legt eine Testkarte an, "
              "drückt jeden Knopf und spielt die Dateibefehle durch.\n\n"
              "Dabei gehen kurz Fenster auf und zu. Das Pedal bleibt "
              "unberührt, es wird nichts an deinen Karten geändert.\n\n"
              "Dauert ein paar Sekunden. Fortfahren?"),
            T("nach jedem Update automatisch prüfen"), test_after_var.get())
        try:
            # dieselbe Einstellung wie in der Zeile „Aktualisierung"
            test_after_var.set(autom)
            einstellung_schreiben("selbsttest_nach_update",
                                  "ja" if autom else "nein")
        except Exception:
            pass
        if not weiter:
            return
        lauf = {"fertig": False, "text": ""}

        def arbeit():
            import subprocess
            versuch = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "funktionstest"],
                capture_output=True, text=True, timeout=300)
            lauf["text"] = (versuch.stdout or "") + (versuch.stderr or "")
            lauf["fertig"] = True

        threading.Thread(target=arbeit, daemon=True).start()

        def warten():
            if not lauf["fertig"]:
                fenster.after(300, warten)
                return
            bericht_fenster(fenster, lauf["text"])

        warten()

    tk.Button(leiste, text=T("Selbsttest …"),
              command=selbsttest_starten).pack(side="right", padx=(0, 12))

    tk.Button(leiste, text=T("Aktualisieren"),
              command=jetzt_aktualisieren).pack(side="right", padx=(0, 12))

    tk.Button(leiste, text=T("Pedal über USB"),
              command=usb_oeffnen).pack(side="right", padx=(0, 12))



    titel_var = tk.StringVar(value=_fenster_titel(basis, UNTERORDNER if basis else ""))
    tk.Label(fenster, textvariable=titel_var,
             font=schrift(15, fest=True, titel=True),
             bg=FARBE_HINTERGRUND, fg=FARBE_SCHRIFT).pack(pady=(12, 2))

    if SPRACHE in UEBERSETZUNGSHINWEIS:
        tk.Label(fenster, text=UEBERSETZUNGSHINWEIS[SPRACHE],
                 bg=FARBE_HINTERGRUND, fg="#c8a24a",
                 font=schrift(11)).pack(pady=(2, 0))

    pfad_var = tk.StringVar(value=basis or T("keine Karte gefunden"))
    tk.Label(fenster, textvariable=pfad_var, bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack()

    # Bei einer geholten Kopie ausdrücklich sagen, daß Änderungen hier nur
    # den Rechner betreffen (17.11.2026).
    kopie_hinweis = tk.StringVar(value="")
    if ist_kopie(basis):
        kopie_hinweis.set(T("Kopie auf dem Rechner — Änderungen hier wirken "
                            "nicht auf das Pedal."))
    tk.Label(fenster, textvariable=kopie_hinweis, bg=FARBE_HINTERGRUND,
             fg="#c8a24a", font=schrift(10), wraplength=1000).pack()

    # Nur-Lesen-Modus (20.11.2026): Ein unbekanntes Gerät wird gelesen, aber
    # nicht beschrieben. Der Hinweis sagt das, und ein Knopf holt den Bericht.
    nur_lesen_var = tk.StringVar(value="")
    tk.Label(fenster, textvariable=nur_lesen_var, bg=FARBE_HINTERGRUND,
             fg="#e0b060", font=schrift(10), wraplength=1000).pack()

    def struktur_melden():
        basis = zustand.get("basis")
        if not basis:
            return
        try:
            ziel, anzahl = struktur_bericht(basis)
        except Exception as fehler:
            messagebox.showinfo(T("Strukturbericht"),
                                T("Der Bericht ließ sich nicht schreiben: %s") % fehler)
            return
        messagebox.showinfo(
            T("Strukturbericht"),
            T("Bericht geschrieben: %s") % ziel + "\n\n"
            + T("Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne "
                "Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.")
            % anzahl + "\n\n"
            + T("Bitte schicken Sie diese Datei an mich oder hängen Sie sie an "
                "ein GitHub-Issue — dann kann ich den Aufbau prüfen und das "
                "Gerät freigeben."))

    knopf_struktur = tk.Button(leiste, text=T("Struktur melden …"),
                               command=struktur_melden, state="disabled")
    knopf_struktur.pack(side="right", padx=(0, 12))

    # Achtungshinweis, wenn hier eine geholte Kopie offen ist und nicht die
    # Karte selbst. Am 16.11.2026 zweimal verwechselt: „Neu laden" zeigte
    # dann die halb geholte Kopie, und das sah nach Durcheinander aus.
    try:
        sicherungsziel = einstellung_lesen("usb_ziel") or os.path.join(
            os.path.expanduser("~/Documents"), "JamMan-Sicherung")
        if basis and os.path.abspath(basis).startswith(
                os.path.abspath(sicherungsziel) + os.sep):
            tk.Label(fenster,
                     text=T("Dies ist eine Kopie vom Pedal, nicht die Karte selbst. "
                            "Über „Karte wählen“ die Karte öffnen."),
                     bg=FARBE_HINTERGRUND, fg="#c8a24a",
                     font=schrift(10), wraplength=1000).pack(pady=(2, 0))
    except Exception:
        pass

    def zeige_ffmpeg_anleitung():
        # Dasselbe Fenster wie im USB-Fenster: scrollbar, kopierbar und
        # im Vordergrund (ersetzt die kleinere eigene Fassung vom 16.11.2026).
        _anleitung_fenster(fenster, ffmpeg_anleitung(),
                           "MP3 und M4A: ffmpeg installieren")

    if ffmpeg_fehlt():
        hinweis = tk.Frame(fenster, bg="#3a3320")
        hinweis.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(hinweis, text=T("MP3 und M4A brauchen ffmpeg -"),
                 bg="#3a3320", fg="#f0d48a").pack(side="left", padx=(10, 4))
        tk.Button(hinweis, text=T("Anleitung"),
                  command=zeige_ffmpeg_anleitung).pack(side="left", pady=4)
        tk.Label(hinweis, text=T("WAV geht auch ohne"),
                 bg="#3a3320", fg="#a0a0a0").pack(side="left", padx=8)

    # Kopfzeile als Variable: sie richtet sich nach denselben Breiten wie die
    # Datenzeilen (20.11.2026).
    kopf_var = tk.StringVar(value="")
    tk.Label(fenster, textvariable=kopf_var,
        font=schrift(12), bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
        anchor="w", justify="left").pack(fill="x", padx=14)

    # Suchen und sortieren (17.11.2026: die Liste ließ sich nur in
    # Ordnerreihenfolge ansehen — bei 200 Plätzen zu wenig).
    such_zeile = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    such_zeile.pack(fill="x", padx=14, pady=(2, 2))
    tk.Label(such_zeile, text=T("Suchen:") + " ", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE, font=schrift(11)).pack(side="left")
    such_var = tk.StringVar(value="")
    # Weißes Feld mit Rahmen und größerer Schrift: auf Windows war das Feld
    # (dunkel auf dunkel, ohne Rahmen) so flach, daß man nicht sah, ob etwas
    # darin steht — getippt wurde, aber nichts erschien (18.11.2026).
    such_eingabe = tk.Entry(such_zeile, textvariable=such_var, width=26,
                            bg="#ffffff", fg="#000000",
                            insertbackground="#000000",
                            relief="solid", borderwidth=1, font=schrift(11))
    such_eingabe.pack(side="left", ipady=4, pady=2)
    tk.Label(such_zeile, text="   " + T("Sortierung") + ": ",
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE,
             font=schrift(11)).pack(side="left")
    # Drei Sortierungen, mehr braucht es nicht (20.11.2026): „Länge" war ein
    # zweiter Name für die Dauer und wurde nie erreicht.
    SORTIERUNG = [("Ordner", "ordner"), ("Name", "name"), ("Dauer", "dauer")]
    # Zuletzt benutzte Sortierung (19.11.2026).
    _sortierung = einstellung_lesen("sortierung") or "ordner"
    if _sortierung not in ("ordner", "name", "dauer"):
        _sortierung = "ordner"
    # Beschriftung übersetzen, Schlüssel behalten (20.11.2026): Vorher stand im
    # Menü der rohe Schlüssel — „ordner", „laenge" — und war damit in jeder
    # Sprache falsch (chinesisch zeigte „order").
    _beschriftung = {T(a): b for a, b in SORTIERUNG}
    _zeige = {b: T(a) for a, b in SORTIERUNG}
    sort_var = tk.StringVar(value=_zeige[_sortierung])
    sort_menue = tk.OptionMenu(such_zeile, sort_var, *list(_beschriftung))
    sort_menue.config(bg=FARBE_LISTE, fg=FARBE_SCHRIFT, relief="flat",
                      highlightthickness=0)
    sort_menue.pack(side="left")

    # Ein Knopf und die Eingabetaste als zweiter Weg: am 18.11.2026 wirkte die
    # Suche auf Windows, als täte sie nichts — so läßt sich prüfen, ob es am
    # Tippen (trace) oder am Filtern liegt.
    such_knopf = tk.Button(such_zeile, text=T("Suchen"),
                           command=lambda: zeilen_anwenden())
    such_knopf.pack(side="left", padx=(8, 0))
    # Fassung neben dem Feld: damit sich zwei entpackte Fassungen im Betrieb
    # unterscheiden lassen (18.11.2026 — es war unklar, welche läuft).
    tk.Label(such_zeile, text="  " + FASSUNG, bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="right")
    such_eingabe.bind("<Return>", lambda e: zeilen_anwenden())

    rahmen = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    rahmen.pack(fill="both", expand=True, padx=14, pady=(0, 6))
    # Listbox statt Zeichenfläche: Tk bringt für Listboxen eine eigene
    # Rad-Behandlung mit (tk::MouseWheel …), für Zeichenflächen nicht.
    # Mit der Zeichenfläche kam das Mausrad auf diesem Rechner nicht mehr
    # durch (am 10.11.2026 mehrfach bestätigt). Der Einfügestrich wird
    # stattdessen als markierte Zeile "hier ablegen" eingeblendet.
    ZEILE_H = 24
    such_var.trace_add("write", lambda *a: zeilen_anwenden())
    def sortierung_wechseln(*_):
        zeilen_anwenden()
        try:
            einstellung_schreiben("sortierung",
                                  _beschriftung.get(sort_var.get(), "ordner"))
        except Exception:
            pass
    sort_var.trace_add("write", sortierung_wechseln)

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
    # Automatisches Aktualisieren: an, solange nichts anderes gesagt wurde.
    update_auto_var = tk.BooleanVar(
        value=einstellung_lesen("update_auto") != "nein")

    def update_auto_umschalten():
        try:
            einstellung_schreiben("update_auto",
                                  "ja" if update_auto_var.get() else "nein")
        except Exception:
            pass

    # Die Häkchen für „Update beim Start" und „Selbsttest nach Update" stehen
    # jetzt in den Dialogen der beiden Knöpfe (19.11.2026) — die eigene Zeile
    # „Aktualisierung" entfällt dadurch. Die Variablen bleiben, weil die
    # Dialoge sie lesen und schreiben.
    test_after_var = tk.BooleanVar(
        value=einstellung_lesen("selbsttest_nach_update") == "ja")

    # Blättern bekommt eine eigene Zeile (19.11.2026): In einer Zeile mit den
    # drei Voreinstellungen wurden die Knöpfe auf schmalen Bildschirmen
    # abgeschnitten („▲ 5" französisch, „vormerken").
    zeile_blaettern = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    zeile_blaettern.pack(fill="x")
    tk.Label(zeile_blaettern, text=T("Blättern") + ":", bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE, font=schrift(10)).pack(side="left", padx=(14, 6))
    tk.Label(zeile_blaettern, text=T("Platz"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(10, 2))
    platz_eingabe = tk.Entry(zeile_blaettern, width=4, justify="center",
                             bg="#ffffff", fg="#000000",
                             insertbackground="#000000",
                             relief="solid", borderwidth=1)
    platz_eingabe.pack(side="left")
    platz_eingabe.bind("<Return>", lambda e: gehe_zu())
    platz_eingabe.bind("<Escape>", lambda e: (platz_eingabe.delete(0, "end"),
                                              zustand.__setitem__("tippen", ""),
                                              status_var.set("")))
    tk.Button(zeile_blaettern, text=T("nächster"), width=9,
              command=lambda: gehe_zu()).pack(side="left", padx=2)
    # Windows zeichnet Knöpfe nativ und ignoriert highlightbackground.
    # Deshalb sitzt der Knopf in einem eigenen Rahmen, dessen Farbe den
    # vorgemerkten Zustand zeigt - das geht auf allen Systemen.
    rahmen_vormerken = tk.Frame(zeile_blaettern, bg=FARBE_HINTERGRUND)
    rahmen_vormerken.pack(side="left", padx=2)
    knopf_vormerken = tk.Button(rahmen_vormerken, text=T("vormerken"), width=10,
                                command=lambda: vormerken())
    knopf_vormerken.pack(padx=2, pady=2)
    ursprung_rahmen = (knopf_vormerken.cget("highlightthickness"),
                       knopf_vormerken.cget("highlightbackground"))
    # Ohne feste Breite: sonst wurden die Zeichen auf schmalen Bildschirmen
    # abgeschnitten (19.11.2026).
    tk.Button(zeile_blaettern, text="▲ 5",
              command=lambda: seite(-1)).pack(side="left", padx=2)
    tk.Button(zeile_blaettern, text="▼ 5",
              command=lambda: seite(1)).pack(side="left", padx=2)

    # Aufräumen auch ohne vorheriges Sichern erreichbar (19.11.2026):
    # Der Dialog war nur nach „Alles sichern" und im USB-Fenster zu finden.
    def sicherungen_aufraeumen():
        """Sicherungsordner: gemerkt, sonst ~/JamMan-Sicherung (wird angelegt).

        Gefragt wird nur, wenn der gemerkte Ordner nicht mehr da ist und sich
        auch der Standardordner nicht anlegen läßt (19.11.2026).
        """
        ziel = einstellung_lesen("sicherung_pfad")
        if not ziel:
            ziel = os.path.join(os.path.expanduser("~"), "JamMan-Sicherung")
            try:
                os.makedirs(ziel, exist_ok=True)
            except OSError:
                pass
        if not os.path.isdir(ziel):
            ziel = ordner_waehlen(fenster, T("Ordner mit den Sicherungen"))
            if not ziel:
                return
            einstellung_schreiben("sicherung_pfad", ziel)
        def jetzt_sichern():
            if not zustand["basis"]:
                messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
                return
            # Im Hintergrund sichern, Balken im Hauptfaden (19.11.2026).
            def arbeit(melden):
                befehl_sichern(zustand["basis"], ziel, True,
                               melden=lambda a, t: melden(a, t))
            fuehre_im_hintergrund(fenster, T("Sicherung anlegen"), arbeit)
            messagebox.showinfo(T("Sicherung"),
                                T("Fertig. Ordner:\n%s") % ziel)

        fenster_aufraeumen(ziel, fenster, sichern=jetzt_sichern)


    # Tastenkürzel für die häufigsten Wege (19.11.2026). Cmd auf dem Mac,
    # Strg auf Windows und Linux — beide Namen werden gebunden, das schadet
    # auf keinem System.
    def _kuerzel():
        for taste, befehl in (("o", karte_waehlen),
                              ("s", sicherungen_aufraeumen),
                              ("r", neu_laden),
                              ("f", suchen_dialog),
                              ("l", lambda: luecken_schliessen())):
            for mod in ("Command", "Control"):
                try:
                    fenster.bind("<%s-%s>" % (mod, taste), lambda e, b=befehl: b())
                except Exception:
                    pass
    # Erst binden, wenn das Fenster läuft: die meisten Ziele (karte_waehlen,
    # suchen_dialog, luecken_schliessen) werden weiter unten in dieser Funktion
    # definiert (19.11.2026).
    fenster.after(50, _kuerzel)

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

    def zeilen_anwenden(*_):
        """Zeilen nach Suchtext filtern und sortieren, dann anzeigen.

        Fehler werden gemeldet statt verschluckt: eine Ausnahme in einem
        trace-Rückruf zeigt Tk nur in der Konsole, das Fenster bliebe stumm.
        """
        try:
            _zeilen_anwenden()
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                status_var.set(T("Suche fehlgeschlagen: %s") % e)
            except Exception:
                pass

    def _zeilen_anwenden(*_):
        alle = zustand.get("alle") or []
        if not any(z.get("nummer") is not None for z in alle):
            # Rückfall: lieber die gerade angezeigten Zeilen filtern als gar
            # nichts tun (18.11.2026 — die Suche blieb sonst wirkungslos).
            alle = [z for z in (zustand.get("zeilen") or []) if z.get("text")]
        # Das Feld bleibt immer bedienbar. Ein graues Feld war am 18.11.2026
        # die falsche Antwort: es ließ sich nicht einmal hineinschreiben, und
        # niemand sah warum. Statt dessen sagt die Statuszeile, was fehlt.
        hat_patches = any(z.get("nummer") is not None for z in alle)
        if not alle or not hat_patches:
            if such_var.get().strip():
                try:
                    status_var.set(T("Erst eine Karte oder eine geholte Kopie "
                                     "öffnen — dann läßt sich suchen."))
                except Exception:
                    pass
            return
        muster = such_var.get().strip().lower()
        art = _beschriftung.get(sort_var.get(), "ordner")
        # Nenner ist die Zahl der Patches, nicht der Zeilen: Hinweiszeilen
        # zählten sonst mit („1 von 12" bei 11 Patches, 19.11.2026).
        _anzahl_patches = sum(1 for z in alle if z.get("nummer") is not None)
        zeilen = [z for z in alle if z.get("nummer") is not None]
        hinweise = [z for z in alle if z.get("nummer") is None]
        if muster:
            if muster.isdigit():
                # „10“ soll Platz 10 finden — der Ordnername heißt Patch010
                # und enthält die Ziffernfolge „10“ nur zufällig (19.11.2026).
                zeilen = [z for z in zeilen
                          if muster in z["text"].lower()
                          or str(z.get("nummer", "")).startswith(muster)]
            else:
                zeilen = [z for z in zeilen if muster in z["text"].lower()]
        # Verglichen wird mit dem SCHLÜSSEL, nicht mit der Beschriftung
        # (20.11.2026): vorher stand hier „Name"/„Dauer" — beides erreichte nie
        # einen Zweig, die Liste blieb in Ordner-Reihenfolge.
        if art == "name":
            zeilen.sort(key=lambda z: (z.get("name") or "").lower())
        elif art == "dauer":
            zeilen.sort(key=lambda z: -(z.get("dauer") or 0))
        rest = [] if muster else hinweise
        anzeige = zeilen + rest
        if muster:
            if muster.isdigit() and not zeilen:
                anzeige = [*anzeige, {"text": "  " + T("Platz %s ist nicht belegt " "— die Liste hat eine Lücke.") % muster}]
            else:
                anzeige = [*anzeige, {"text": "  " + T("%d von %d Patches passen " "zu „%s“.") % (len(zeilen), _anzahl_patches, muster)}]
        liste_setzen(anzeige)
        if muster:
            print("  Suche: %d von %d Patches" % (
                len(zeilen), sum(1 for z in alle if z.get("nummer") is not None)))

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

    def seite(richtung):
        """Blätterknöpfe: fünf Zeilen je Druck.

        Eine ganze Seite war zu grob - die Liste sprang fast ans Ende
        (Rückmeldung vom 10.11.2026). Ganze Seiten gibt es über Bild auf/ab.
        """
        zustand["nutzer_scroll"] = time.time()
        liste.yview_scroll(richtung * 5, "units")
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
        if i and zustand["nummern"] and 0 <= i[0] < len(zustand["nummern"]):
            vorher = zustand["nummern"][i[0]]
        zeilen = []
        zustand["nummern"] = []
        # „Neu laden“ muß auch dann helfen, wenn der gemerkte Ordner
        # verschwunden ist — etwa weil die Karte erst nach dem Start
        # eingesteckt wurde (16.11.2026: Knopf wirkte wirkungslos).
        if not zustand["basis"] or not os.path.isdir(zustand["basis"]):
            zustand["basis"] = karte_finden()
        if not zustand["basis"]:
            pfad_var.set(T("keine Karte gefunden"))
            try:
                titel_var.set(T("keine Karte"))
            except Exception:
                pass
            zustand["alle"] = []
            liste_setzen([
                {"text": "  " + T("Keine Karte im Rechner gefunden.")},
                {"text": ""},
                {"text": "  " + T("Karte im Rechner:  „Karte wählen“ drücken.")},
                {"text": "  " + T("Karte im Pedal:    oben rechts „Pedal über USB“ — "
                                  "dort sind Karte und interner Speicher erreichbar.")},
            ])
            fenster.title(T("JamMan Card Tool") + " — " + T("keine Karte"))
            zeilen_anwenden()
            return
        pfad_var.set(zustand["basis"])
        # Nur-Lesen-Modus anzeigen und den Bericht-Knopf freigeben
        # (20.11.2026). Achtung: Einrückung auf Funktionsebene — beim ersten
        # Einbau stand sie vier Spalten zu weit links, und die Zeilenliste
        # blieb dadurch leer (selbst gesehen, nicht vermutet).
        try:
            if NUR_LESEN:
                nur_lesen_var.set(
                    T("Nur-Lesen-Modus: unbekanntes Gerät „%s“. Die Karte wird "
                      "gelesen; geschrieben wird nichts, weil der Aufbau nicht "
                      "belegt ist. Mit „Struktur melden …“ können Sie ihn belegen.")
                    % (UNTERORDNER or "?"))
                knopf_struktur.config(state="normal")
            else:
                nur_lesen_var.set("")
                knopf_struktur.config(state="disabled")
        except Exception:
            pass
        try:
            titel_var.set(_fenster_titel(zustand["basis"], UNTERORDNER))
        except Exception:
            pass
        rohdaten = []
        for nummer, name, pfad in patches(zustand["basis"]):
            w = phrase_wav(pfad)
            stop, rhy, loop, rueck, bpm = patch_einstellungen(pfad)
            dauer = wav_info(w)[0] if w else 0
            marke = "  ← über 10 min" if dauer > MAX_LOOP_SEKUNDEN else ""
            # Rückwärts und Tempo gehörten schon immer ins Pedal, standen aber
            # nirgends (18.11.2026): „↺" hinter der Wiedergabe, BPM als Spalte.
            wiedergabe = T(loop) + (" ↺" if rueck else "")
            tempo = ("%d BPM" % round(bpm)) if bpm else "—"
            rohdaten.append((nummer, name, patch_name(pfad)[:28],
                             sekunden(dauer) if w else "—", T(stop),
                             wiedergabe, tempo, T(rhy), marke,
                             dauer, bpm, rueck, bool(w)))
        # Bündig setzen: Breiten nach dem längsten Wert DIESER Sprache
        # (20.11.2026 — vorher standen sie fest im Code und paßten nur deutsch).
        felder = [(r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rohdaten]
        kopfzeile, gesetzt = spalten_layout(
            [T("Ordner"), T("Name"), T("Dauer"), T("Stoppart"), T("Wdg."),
             T("Tempo"), T("Rhythmus")], felder)
        try:
            kopf_var.set(kopfzeile or "")
        except Exception:
            pass
        for (nummer, name, _pname, _dauer, _stop, _wdg, _tempo, _rhy, marke,
             dauer, bpm, rueck, hat_ton), zeile in zip(rohdaten, gesetzt):
            zeilen.append({"text": zeile + marke, "nummer": nummer, "name": name,
                           "dauer": dauer, "bpm": bpm, "rueck": rueck,
                           "hat_ton": hat_ton})
            zustand["nummern"].append(nummer)
        ohne_ton = sum(1 for zeile in zeilen if not zeile.get("hat_ton", True))
        if ohne_ton:
            zeilen.append({"text": "  " + T("%d Patches ohne Ton — nur die "
                                            "Beschreibung ist geholt.") % ohne_ton})
        zustand["alle"] = zeilen
        zeilen_anwenden()
        # Lücken sichtbar machen (19.11.2026): Die Liste benennt nicht um —
        # sie sagt nur, wo eine Platznummer fehlt, und wie man sie schließt.
        try:
            luecken = luecken_in(zustand["basis"]) if zustand.get("basis") else []
            if luecken:
                anzeige_luecken = ", ".join(str(n) for n in luecken[:6])
                mehr = " …" if len(luecken) > 6 else ""
                schluessel = ("Lücke bei Platz %s%s — „Lücken schließen“ "
                              "rückt die folgenden Plätze nach vorn."
                              if len(luecken) == 1 else
                              "Lücken bei den Plätzen %s%s — „Lücken schließen“ "
                              "rückt die folgenden Plätze nach vorn.")
                status_var.set(T(schluessel) % (anzeige_luecken, mehr))
        except Exception:
            pass
        if zustand["nummern"]:
            fenster.title(T("JamMan Card Tool") + " — %s (%d)" % (
                T("Karte und interner Speicher"), len(zustand["nummern"])))
            if not zustand["nummern"] and zustand["basis"] and not zustand.get("lesefehler_gemeldet"):
                fehler = karte_lesefehler(zustand["basis"])
                if fehler:
                    zustand["lesefehler_gemeldet"] = True
                    messagebox.showerror(
                        T("Karte nicht lesbar"),
                        T("Die Karte ist eingebunden, aber das Dateisystem liefert\n"
                          "Lesefehler:\n\n%s\n\n"
                          "Das liegt an der Karte oder am Einhängepunkt, nicht am\n"
                          "Werkzeug.\n\n"
                          "Erste Schritte:\n"
                          "  - Karte im JamMan einschalten (das Pedal prüft sie)\n"
                          "  - dmesg | tail -20\n"
                          "  - aushängen und prüfen: sudo fsck.vfat -n <gerät>\n"
                          "    bzw. sudo exfatfsck <gerät>")
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
        zeit_var.set(T("%s   •   %s   •   Klick in die Spur springt dorthin")
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
            folge = [cue, *nummern[j + 1:]]
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
                messagebox.showerror(T("Fehler"), T("Bitte eine Platznummer eintragen."))
                return
        if nummer not in nummern:
            messagebox.showerror(T("Fehler"), "Platz %d gibt es nicht." % nummer)
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
                    messagebox.showerror(T("Fehler"), T("Bitte eine Platznummer eintragen."))
                    return
        if nummer not in nummern:
            messagebox.showerror(T("Fehler"),
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
        p = ordner_waehlen(fenster, T("Speicherkarte des JamMan wählen"),
                               start or None)
        if not p:
            return
        try:
            zustand["basis"] = karte_pruefen(p)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror(T("Keine JamMan-Karte"), str(e))

    def wahl_wert(var):
        """Angezeigten Text zurück auf den internen Wert führen."""
        wert = var.get()
        for intern, anzeige in (("wie Vorlage", "wie Vorlage"),
                                ("fade", "fade"),
                                ("instant", "sofort"),
                                ("finish", "ende"),
                                ("silence", "ohne"),
                                ("kick", "klick"),
                                ("loop", "loop"),
                                ("single", "single")):
            if wert in (T(anzeige), anzeige, T(intern), intern):
                return intern
        return wert

    def gewaehlt():
        """Platznummer der markierten Zeile — None bei Hinweiszeilen.

        Hinweiszeilen (etwa „N Patches ohne Ton …") haben keine Nummer; der
        Zugriff auf zustand["nummern"] lief dabei aus dem Index
        (18.11.2026: IndexError beim Klick auf eine solche Zeile).
        """
        i = liste_auswahl()
        if not i:
            return None
        if 0 <= i[0] < len(zustand["nummern"]):
            return zustand["nummern"][i[0]]
        return None

    def abspielen():
        # Läuft schon etwas, wird es beendet und der gewählte Patch
        # gestartet. Die Beschriftung bleibt "Anhören" - sonst stünden
        # zwei Knöpfe mit "Stopp" nebeneinander (am 10.11.2026 gesehen).
        if laeuft():
            stoppen(merker_weg=False)
        n = gewaehlt()
        if not n:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch in der Liste wählen."))
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        w = phrase_wav(alle[n])
        if not w:
            messagebox.showerror(T("Fehler"), "Patch%d hat keine WAV." % n)
            return
        # Nahtloses Weiterspielen: lieber eine einzige Datei aus mehreren
        # Patches bauen als jeden Patch einzeln zu starten (das ergab eine
        # hörbare Pause, Rückmeldung vom 10.11.2026).
        zustand["abschnitte"] = []
        datei, abschnitte = (None, [])
        if weiter_var.get() or zustand.get("vorgemerkt"):
            with _Zeit("Kette bauen (Weiterspielen)"):
                datei, abschnitte = kette_bauen(n, 0.0, zustand.get("vorgemerkt"))
        if datei:
            zustand["spieler"] = abspielen_datei(datei)
            zustand["temp"] = datei
            zustand["abschnitte"] = abschnitte
        else:
            # Rückwärts-Patches auch rückwärts vorspielen: das Pedal macht
            # das anhand von IsReversed, das Fenster spielte sonst das
            # Original (Rückmeldung 18.11.2026).
            _, _, _, rueck, _ = patch_einstellungen(alle[n])
            probe = wav_rueckwaerts(w) if rueck else None
            if rueck and not probe:
                status_var.set(T("Rückwärts: ohne ffmpeg keine Hörprobe — "
                                 "das Pedal spielt es trotzdem rückwärts."))
            zustand["spieler"] = abspielen_datei(probe or w)
            if probe:
                zustand["temp"] = probe
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
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        datei = datei_waehlen(fenster, T("Audiodatei wählen"), "*.wav *.aif *.aiff *.aifc *.caf *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma",
                                einstellung_lesen("audio_pfad") or None)
        einstellung_schreiben("audio_pfad", os.path.dirname(datei))
        if not datei:
            return

        # Zu lange Dateien: gleich anbieten zu kürzen statt nur abzulehnen
        kuerzen = False
        dauer_datei = wav_info(datei)[0]
        if dauer_datei > MAX_LOOP_SEKUNDEN:
            if not frage_ja_nein(fenster, "Datei zu lang",
                    "Die Datei ist %.1f Minuten lang.\n\n"
                    "Der JamMan Solo XT nimmt höchstens %d Minuten pro Loop an\n"
                    "(Handbuch, Seite 60). Längere Loops erscheinen auf der Karte,\n"
                    "werden vom Pedal aber nicht erkannt und können verschwinden.\n\n"
                    "Auf 9:59 kürzen? Der Schnitt liegt am Ende; die letzten\n"
                    "12 ms werden ausgeblendet, damit es nicht knackt."
                    % (dauer_datei / 60.0, MAX_LOOP_SEKUNDEN // 60)):
                return
            kuerzen = True

        if not frage_ja_nein(fenster, "Aufspielen",
                "Die Karte wird verändert:\n\n%s\n\nFortfahren?"
                % os.path.basename(datei)):
            return
        try:
            gain_wert, normieren = pegel_lesen()
        except ValueError:
            messagebox.showerror(T("Fehler"),
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
                    messagebox.showerror(T("Fehler"), T("Bei Fade am Ende bitte eine Zahl in Sekunden eintragen."))
                    return
                if fade_wert <= 0:
                    fade_wert = None
            befehl_aufspielen(zustand["basis"], datei, None, True, st, rh, wg,
                              fade_wert, gain_wert, normieren,
                              begrenzen_var.get(), kuerzen=kuerzen)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def sichern():
        if not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        # Vorgabe: zuletzt benutzter Ordner, sonst ~/JamMan-Sicherung
        # (19.11.2026: „der Standardordner als Vorgabe wäre gut").
        vorgabe = einstellung_lesen("sicherung_pfad")
        if not vorgabe:
            vorgabe = os.path.join(os.path.expanduser("~"), "JamMan-Sicherung")
            try:
                os.makedirs(vorgabe, exist_ok=True)
            except OSError:
                vorgabe = os.path.expanduser("~")
        ziel = ordner_waehlen(fenster, T("Zielordner für die Sicherung"), start=vorgabe)
        if not ziel:
            return
        befehl_sichern(zustand["basis"], ziel, True)
        einstellung_schreiben("sicherung_pfad", ziel)
        messagebox.showinfo(T("Sicherung"), T("Fertig. Ordner:\n%s") % ziel)
        # Gleich aufräumen anbieten — direkt nach dem Sichern ist der
        # Augenblick, in dem man alte Stände wegwerfen will.
        if frage_ja_nein(fenster, T("Sicherungen aufräumen"),
                         T("Alte Sicherungen in diesem Ordner ansehen und "
                           "löschen?")):
            fenster_aufraeumen(ziel, fenster, karte=zustand.get("basis"))

    def umbenennen():
        """Namen des gewählten Patches ändern."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch in der Liste wählen."))
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        alt = patch_name(alle[n])
        neu = simpledialog.askstring("Umbenennen",
                "Neuer Name für Patch%d:" % n, initialvalue=alt, parent=fenster)
        if neu is None:
            return
        neu = neu.strip()
        if not neu:
            messagebox.showinfo(T("Hinweis"), T("Der Name darf nicht leer sein."))
            return
        if neu == alt:
            return
        try:
            befehl_aendern(zustand["basis"], n, True, None, None, None, neu)
            neu_laden()
            status_var.set("umbenannt: %s → %s" % (alt, neu))
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def zuruecknehmen():
        """Sicherung des gewählten Patches zurückholen (z. B. Fade rückgängig)."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch in der Liste wählen."))
            return
        ordner = os.path.expanduser(SICHERUNG_WURZEL)
        kandidaten = zuruecknehmen_kandidaten(ordner, n)
        if not kandidaten:
            messagebox.showerror(T("Fehler"),
                "Für Patch%d liegt in %s keine Sicherung mit Aufnahme."
                % (n, ordner))
            return
        quelle = kandidaten[0]
        # Bei einer Vollsicherung liegt der Patch eine Ebene tiefer
        # (…/JamManSicherung_…/JamManSoloXT/Patch02) — dann ist der Name des
        # Sicherungsordners eine Ebene höher (19.11.2026).
        ordner_der_sicherung = os.path.dirname(quelle)
        if os.path.basename(ordner_der_sicherung) == UNTERORDNER:
            ordner_der_sicherung = os.path.dirname(ordner_der_sicherung)
        name = os.path.basename(ordner_der_sicherung)
        # Reihenfolge beachten: „JamManSicherung_" enthält „Sicherung_",
        # sonst bliebe „JamMan2026-…" übrig (19.11.2026).
        stand = name.replace("JamManSicherung_", "").replace("Sicherung_", "")
        stand = stand.replace("_", " ")
        nur_einstellungen = ist_einstellungssicherung(quelle)
        if nur_einstellungen:
            frage = T("Patch%d: Name, Stoppart, Rhythmus und Wiedergabe "
                      "aus dieser Sicherung zurückholen?\n\n  %s\n\n"
                      "Die Aufnahme bleibt, wie sie ist. Der jetzige Stand "
                      "wird vorher gesichert.") % (n, stand)
        else:
            frage = T("Patch%d aus dieser Sicherung wiederherstellen?"
                      "\n\n  %s\n\n"
                      "Damit lässt sich zum Beispiel ein Fade zurücknehmen. "
                      "Der jetzige Stand wird vorher gesichert.") % (n, stand)
        if not frage_ja_nein(fenster, "Zurücknehmen", frage):
            return
        try:
            with _Zeit("zurücknehmen, Patch%d" % n):
                if ist_einstellungssicherung(quelle):
                    befehl_zuruecknehmen_einstellungen(zustand["basis"], n,
                                                       True, quelle)
                else:
                    befehl_zuruecknehmen(zustand["basis"], n, True)
            with _Zeit("Liste neu aufbauen (nach zurücknehmen)"):
                neu_laden()
            wahl_zuruecksetzen()
            status_var.set(T("Patch%d zurückgeholt") % n)
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def pegel_zurueck():
        """Pegeländerung aus einem Protokoll zurückrechnen."""
        if not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        ordner = os.path.expanduser(SICHERUNG_WURZEL)
        # Liste statt Dateidialog (19.11.2026, Wunsch: größter Bedienkomfort):
        # Datum, Anlaß und Anzahl der Patches stehen in der Zeile.
        datei = fenster_pegelprotokolle(ordner, fenster)
        if not datei:
            return
        eintraege = lies_pegelprotokoll(datei)
        if not eintraege:
            messagebox.showerror(T("Fehler"),
                                 T("Im Protokoll stehen keine Einträge."))
            return
        if len(eintraege) == 1:
            was = T("Ein Patch (Platz %s)") % eintraege[0]["ordner"].replace("Patch", "")
        else:
            was = T("%d Patches") % len(eintraege)
        if not frage_ja_nein(fenster, "Pegeländerung zurücknehmen",
                T("%s aus diesem Protokoll werden umgekehrt:\n\n%s\n\n"
                  "Nur diese Patches werden vorher gesichert. Fortfahren?")
                % (was, os.path.basename(datei))):
            return
        try:
            befehl_pegel_umkehren(zustand["basis"], datei, True)
            neu_laden()
            status_var.set(T("Pegel zurückgerechnet"))
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def datei_teilen():
        """Eine Datei aufteilen - alle Einstellungen stehen im Wellenform-Fenster."""
        if not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        datei = datei_waehlen(fenster, T("Audiodatei zum Teilen auswählen"),
                              "*.wav *.aif *.aiff *.aifc *.caf *.mp3 *.m4a *.aac *.flac *.ogg *.opus")
        if not datei:
            return
        if wav_info(datei)[0] <= 0:
            messagebox.showerror(T("Fehler"), T("Die Datei ist nicht lesbar."))
            return
        gewaehlt = teilen_fenster(datei, fenster)
        if not gewaehlt:
            return
        marken = gewaehlt["marken"]
        anzahl = len(marken) + 1 if marken else gewaehlt["teile"]
        try:
            befehl_teilen(zustand["basis"], datei,
                          os.path.splitext(os.path.basename(datei))[0],
                          True, anzahl, None, None, None, None,
                          sinnvoll=gewaehlt["pausen"],
                          marker=marken or None,
                          schwelle=gewaehlt["schwelle"] / 100.0,
                          min_pause=gewaehlt["pause"],
                          fade_naht=gewaehlt["fade"],
                          alle_pausen=False)
            neu_laden()
            status_var.set(T("in %d Patches geteilt") % anzahl)
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def alle_normieren():
        """Alle Patches auf gleiche Lautheit bringen."""
        if not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        # Text berichtigt (19.11.2026): Es wird nicht mehr die ganze Karte
        # gesichert, sondern nur, was sich ändert. Der alte Satz („die ganze
        # Karte … ein bis zwei Minuten") hätte die Laufzeit falsch angekündigt.
        if not frage_ja_nein(fenster, "Alle Patches normieren",
                "Alle Patches werden auf gleiche Lautheit gebracht:\n\n"
                "  Ziel -18 dB Lautheit, Spitze höchstens -1 dB\n\n"
                "Patches, die schon passen, bleiben unangetastet. Nur die\n"
                "übrigen werden vorher gesichert und neu geschrieben.\n\n"
                "Fortfahren?"):
            return
        balken = Fortschritt(fenster, T("Alle normieren"))
        def melde(anteil, text=""):
            if isinstance(anteil, str):
                status_var.set(anteil)
            else:
                balken.setzen(anteil, text)
                status_var.set(text)
            fenster.update_idletasks()
        try:
            befehl_alle_normieren(zustand["basis"], True, fortschritt=melde)
            neu_laden()
            status_var.set("alle Patches normiert")
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))
        finally:
            balken.schliessen()

    def platz_aendern():
        """Patch auf eine bestimmte Platznummer legen."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch in der Liste wählen."))
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
            messagebox.showerror(T("Fehler"), str(e))

    def luecken_schliessen():
        """Lücken zwischen den belegten Plätzen schließen — nur auf Knopfdruck.

        Anlaß (19.11.2026): Die Liste soll nicht von selbst umbenennen; ein
        Werkzeug, das beim bloßen Hinsehen 25 Ordner umbenennt, ist gefährlich.
        Deshalb: Rückfrage, Sicherung der Zuordnung, dann aufrücken.
        """
        basis = zustand.get("basis")
        if not basis:
            messagebox.showinfo(T("Hinweis"), T("Erst eine Karte wählen."))
            return
        luecken = luecken_in(basis)
        if not luecken:
            messagebox.showinfo(T("Hinweis"),
                                T("Es gibt keine Lücken — die Plätze sind schon "
                                  "lückenlos belegt."))
            return
        zuege = aufruecken_zuege(basis)
        beispiele = ", ".join(T("Patch%d → Patch%d") % (a, b) for a, b in zuege[:4])
        weitere = "" if len(zuege) <= 4 else " …"
        if not frage_ja_nein(
                fenster, T("Lücken schließen"),
                T("%d Patches rücken nach vorn: %s%s\n\n"
                  "Die Reihenfolge bleibt, nur die Platznummern ändern sich. "
                  "Die Zuordnung wird vorher gesichert. Setlist-Dateien, die auf "
                  "Platznummern zeigen, stimmen danach nicht mehr. Fortfahren?")
                % (len(zuege), beispiele, weitere)):
            return
        try:
            plaetze_aufruecken_lokal(basis)
        except SystemExit as fehler:
            messagebox.showerror(T("Fehler"), str(fehler))
            return
        except Exception as fehler:
            messagebox.showerror(T("Fehler"), str(fehler))
            return
        neu_laden()
        status_var.set(T("Aufgerückt: %d Patches") % len(zuege))
        if frage_ja_nein(fenster, T("Setlists anpassen"),
                         T("Sollen vorhandene Setlist-Dateien auf die neuen "
                           "Platznummern umgeschrieben werden?")):
            try:
                _, anzahl = fenster_setlists(zuege, fenster)
                if anzahl:
                    status_var.set(T("Setlists angepaßt: %d Datei(en)") % anzahl)
            except Exception as fehler:
                messagebox.showerror(T("Fehler"), str(fehler))

    def tauschen():
        """Zwei markierte Patches tauschen die Plätze."""
        i = liste_auswahl()
        if len(i) != 2:
            messagebox.showinfo(T("Hinweis"),
                T("Zum Tauschen genau ZWEI Patches markieren:\n"
                  "ersten anklicken, zweiten mit gedrückter Cmd-Taste (⌘)."))
            return
        alle = [nr for nr, _, _ in patches(zustand["basis"])]
        a, b = alle[i[0]], alle[i[1]]
        if not frage_ja_nein(fenster, "Tauschen",
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
            messagebox.showerror(T("Fehler"), str(f))

    def suchen_dialog():
        """Suchtext über einen Dialog setzen — zweiter Weg zum Filtern.

        Anlaß (18.11.2026): Auf Windows ließ sich in das Suchfeld über der
        Liste nicht schreiben. Dieser Dialog kommt ohne das Feld aus.
        """
        alt = such_var.get().strip()
        text = _frage_text(fenster, T("Suchen"),
                           T("Text im Namen (leer lassen = alles zeigen):"), alt)
        if text is None:
            return
        such_var.set(text)
        zeilen_anwenden()

    def beschreibung_aendern():
        n = gewaehlt()
        if not n or not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch wählen."))
            return
        alle = dict((nr, pf) for nr, _, pf in patches(zustand["basis"]))
        if n not in alle:
            return
        beschreibung_fenster(fenster, alle[n])
        neu_laden()

    def einstellungen_anwenden():
        """Setzt die drei Auswahlfelder am GEWÄHLTEN Patch."""
        n = gewaehlt()
        if not n:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch in der Liste wählen."))
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
                messagebox.showerror(T("Fehler"), T("Bei Fade am Ende bitte eine Zahl in Sekunden eintragen."))
                return
            if fade_wert <= 0:
                fade_wert = None
        try:
            gain_wert, normieren = pegel_lesen()
        except ValueError:
            messagebox.showerror(T("Fehler"), T("Beim Pegel bitte eine Zahl in dB eintragen (z. B. -3)."))
            return
        if not (st or rh or wg or fade_wert or gain_wert or normieren):
            messagebox.showinfo(T("Hinweis"), T("Oben steht überall „wie Vorlage - dann gibt es nichts zu ändern."))
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
        if not frage_ja_nein(fenster, "Einstellungen anwenden",
                "Patch%d bekommt:\n\n%s\n\nVorher wird gesichert. Fortfahren?"
                % (n, "\n".join(teile))):
            return
        # Im Hintergrund arbeiten, damit das Fenster nicht einfriert
        # (19.11.2026: Beachball beim Anwenden auf einer Karte im Leser).
        def arbeit(melden):
            befehl_aendern(zustand["basis"], n, True, st, rh, wg, None, None,
                           fade_wert, gain_wert, normieren, begrenzen_var.get(),
                           melden=melden)
        try:
            fuehre_im_hintergrund(fenster, T("Einstellungen anwenden"), arbeit)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def verschieben(schritt):
        n = gewaehlt()
        if not n or not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch wählen."))
            return
        alle = [nr for nr, _, _ in patches(zustand["basis"])]
        platz = alle.index(n) + 1 + schritt if n in alle else 0
        if not (1 <= platz <= len(alle)):
            return
        try:
            befehl_verschieben(zustand["basis"], alle.index(n) + 1, platz, True)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def loeschen():
        n = gewaehlt()
        if not n or not zustand["basis"]:
            messagebox.showinfo(T("Hinweis"), T("Erst einen Patch wählen."))
            return
        if ist_kopie(zustand["basis"]):
            frage_text = (T("Patch%d wird aus der Kopie auf dem Rechner entfernt. "
                            "Das Pedal bleibt unverändert. Fortfahren?") % n)
        else:
            frage_text = (T("Patch%d wird von der Karte entfernt. Vorher wird er "
                            "auf dem Rechner gesichert. Fortfahren?") % n)
        if not frage_ja_nein(fenster, T("Löschen"), frage_text):
            return
        try:
            befehl_loeschen(zustand["basis"], n, True)
            neu_laden()
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    def karte_auswerfen():
        if laeuft():
            stoppen()
        if not frage_ja_nein(fenster, "Auswerfen",
                "Karte auswerfen? Danach kann sie abgezogen werden."):
            return
        quelle = zustand["basis"] or pfad_var.get()
        try:
            befehl_auswerfen(quelle if os.path.isdir(quelle) else standard_karte())
            zustand["basis"] = None
            pfad_var.set(T("keine Karte gefunden"))
            liste_setzen([{"text": "  Karte ausgeworfen. Neue Karte einlegen und Karte wählen."}])
            zustand["welle"] = []
            zustand["dauer"] = 0.0
            zustand["marke"] = None
            zeichne_welle()
            zeit_var.set(T("kein Patch gewählt"))
            fenster.title(T("JamMan Card Tool") + " — " + T("Karte und interner Speicher"))
        except SystemExit as e:
            messagebox.showerror(T("Fehler"), str(e))

    # --- Auswahlfelder -----------------------------------------------------
    wahl = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    wahl.pack(pady=(4, 2))
    # Gilt für beide Wege: "Datei aufspielen" und "Einstellungen anwenden".
    # "Beim Aufspielen" war irreführend, weil dieselben Felder auch einen
    # vorhandenen Patch ändern.
    tk.Label(wahl, text=T("Einstellungen:"), bg=FARBE_HINTERGRUND,
             fg=FARBE_LEISE).pack(side="left", padx=(0, 10))
    # Angezeigt wird dasselbe Wort wie in der Liste (17.11.2026: dort stand
    # „sofort“, im Feld „instant“ — das verwirrte zu Recht).
    for beschriftung, werte in (
            ("Stoppart", ("wie Vorlage", "fade", "sofort", "ende")),
            ("Rhythmus", ("wie Vorlage", "ohne", "klick")),
            ("Wiedergabe", ("wie Vorlage", "loop", "single",
                            "loop \u21ba",
                            "single \u21ba"))):
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

    def wahl_zuruecksetzen():
        """Die drei Felder wieder auf „wie Vorlage" stellen.

        Anlaß (19.11.2026): Nach dem Zurücknehmen blieb im Feld „Wiedergabe"
        das Zeichen „loop ↺" stehen, obwohl der Patch längst wieder Loop war —
        die Felder beschreiben eine Absicht, nicht den Zustand des Patches.
        """
        for var in (stopp_wahl, rhythmus_wahl, wiedergabe_wahl):
            try:
                var.set(T("wie Vorlage"))
            except Exception:
                pass

    # --- Knöpfe: zwei Reihen, damit nichts abgeschnitten wird ---------------
    def knopfreihe(zeile):
        rahmen2 = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
        rahmen2.pack(fill="x", padx=14, pady=2)
        return rahmen2

    knopf_stopp = None

    knopf_refs = {}

    def baue_reihe(nummer, eintraege):
        rahmen3 = knopfreihe(nummer)
        for spalte, (beschriftung, befehl) in enumerate(eintraege):
            knopf = tk.Button(rahmen3, text=knopf_text(T(beschriftung),
                                                       grenze=22 if knopf_groesse > 9 else 17),
                              command=befehl, font=schrift(knopf_groesse))
            knopf_refs[beschriftung] = knopf
            knopf.grid(row=0, column=spalte, sticky="ew", padx=3)
            # Ohne `uniform`: die Spalte bekommt mindestens die Breite ihrer
            # Beschriftung und erst danach den zusätzlichen Platz. Mit
            # gleichbreiten Spalten wurden lange Texte abgeschnitten.
            rahmen3.grid_columnconfigure(spalte, weight=1)
        return rahmen3

    # Knopfschrift richtet sich nach der Bildschirmbreite (19.11.2026):
    # Auf einem 1280er Bildschirm wurden lange französische Beschriftungen
    # sonst abgeschnitten.
    knopf_groesse = 11
    try:
        _breite = fenster.winfo_screenwidth()
        if _breite < 1300:
            knopf_groesse = 9
        elif _breite < 1600:
            knopf_groesse = 10
    except Exception:
        pass

    baue_reihe(1, (# Karte: wählen, neu lesen
                   ("Karte wählen", karte_waehlen),
                   ("Neu laden", neu_laden),
                   # Hinein: Datei aufspielen
                   ("Datei aufspielen", aufspielen),
                   # Sicherheit: Verwalten und einen Schritt zurück
                   ("Sicherungen verwalten …", sicherungen_aufraeumen),
                   ("Zurücknehmen (letzter Schritt)", zuruecknehmen),
                   # Werkzeug: Pegel. Die Suche steht im Feld darüber;
                   # den Dialog gibt es weiter über Cmd/Strg+F (19.11.2026).
                   ("Pegeländerung zurücknehmen …", pegel_zurueck)))

    reihe2 = tk.Frame(fenster, bg=FARBE_HINTERGRUND)
    reihe2.pack(fill="x", padx=14, pady=2)
    for spalte, (beschriftung, befehl) in enumerate(
            (("Anhören", abspielen),
             ("Stopp", stoppen),
             ("Umbenennen", umbenennen),
             ("Platz ändern", platz_aendern),
             ("Lücken schließen", luecken_schliessen),
             ("Einstellungen anwenden", einstellungen_anwenden),
             ("Beschreibung …", beschreibung_aendern))):
        knopf = tk.Button(reihe2, text=knopf_text(T(beschriftung),
                                                  grenze=22 if knopf_groesse > 9 else 17),
                          command=befehl, font=schrift(knopf_groesse))
        knopf_refs[beschriftung] = knopf
        knopf.grid(row=0, column=spalte, sticky="ew", padx=3)
        reihe2.grid_columnconfigure(spalte, weight=1)
        if beschriftung == "Stopp":
            knopf_stopp = knopf

    baue_reihe(3, (("▲ nach oben", lambda: verschieben(-1)),
                   ("▼ nach unten", lambda: verschieben(1)),
                   ("Tauschen", tauschen),
                   ("Datei teilen", datei_teilen),
                   ("Alle normieren", alle_normieren),
                   # „Löschen" steht am Rand, nicht mehr zwischen zwei
                   # harmlosen Knöpfen (19.11.2026).
                   ("Karte auswerfen", karte_auswerfen),
                   ("Löschen", loeschen)))

    # Diese Knöpfe SCHREIBEN — bei einem unbekannten Gerät sind sie gesperrt
    # (Nur-Lesen-Modus, 20.11.2026).
    schreibend = ("Umbenennen", "Platz ändern", "Tauschen", "Löschen",
                  "Datei teilen", "▲ nach oben", "▼ nach unten",
                  "Einstellungen anwenden", "Beschreibung …",
                  "Datei aufspielen", "Alle normieren",
                  "Lücken schließen", "Zurücknehmen (letzter Schritt)",
                  "Pegeländerung zurücknehmen …", "Karte auswerfen")
    # 21.11.2026: „Datei teilen" braucht KEINEN gewählten Patch - die Datei wird
    # im Fenster ausgesucht. Vorher war der Knopf ohne Auswahl gesperrt, obwohl
    # Teilen nichts mit dem gewählten Platz zu tun hat.
    # „Anhören" ist aus der Liste der schreibenden Knöpfe genommen: Abspielen
    # ändert nichts, es darf auch im Nur-Lesen-Modus gehen.
    braucht_auswahl = ("Anhören", "Umbenennen", "Platz ändern", "Tauschen",
                       "Löschen", "▲ nach oben", "▼ nach unten",
                       "Einstellungen anwenden", "Beschreibung …",
                       # 19.11.2026: „Zurücknehmen" und „Beschreibung …" brauchten
                       # ebenfalls einen gewählten Patch, blieben aber anklickbar
                       # und antworteten mit „Erst einen Patch wählen".
                       "Zurücknehmen (letzter Schritt)")

    # Ohne Karte sind auch diese Knöpfe sinnlos — sie würden nur melden
    # „Erst eine Karte wählen" (21.11.2026).
    braucht_karte = ("Neu laden", "Datei aufspielen", "Datei teilen",
                     "Alle normieren", "Lücken schließen", "Karte auswerfen",
                     "Pegeländerung zurücknehmen …", "Zurücknehmen (letzter Schritt)",
                     "Anhören", "Stopp")

    def knopf_zustand():
        if not zustand.get("basis"):
            for name in braucht_karte:
                k = knopf_refs.get(name)
                if k is not None:
                    k.config(state="disabled")
            return
        hat = bool(liste_auswahl())
        for name in braucht_auswahl:
            k = knopf_refs.get(name)
            if k is not None:
                k.config(state="normal" if hat else "disabled")
        # Nur-Lesen: alles Schreibende bleibt gesperrt, auch mit Auswahl.
        if NUR_LESEN:
            for name in schreibend:
                k = knopf_refs.get(name)
                if k is not None:
                    k.config(state="disabled")

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
    tk.Checkbutton(wahl2, text=T("auf −1 dB Spitze normalisieren"),
                   variable=normieren_var, bg=FARBE_HINTERGRUND,
                   fg=FARBE_LEISE, activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left", padx=(0, 16))
    begrenzen_var = tk.BooleanVar(value=True)
    tk.Checkbutton(wahl2, text=T("Anhebung höchstens auf 0 dB"),
                   variable=begrenzen_var, bg=FARBE_HINTERGRUND,
                   fg=FARBE_LEISE, activebackground=FARBE_HINTERGRUND,
                   activeforeground=FARBE_SCHRIFT,
                   selectcolor=FARBE_HINTERGRUND).pack(side="left")

    if knopf_stopp is not None:
        knopf_stopp.config(state="disabled")

    tk.Label(fenster, textvariable=status_var, bg=FARBE_HINTERGRUND,
             fg=FARBE_TON).pack(pady=(6, 0))
    tk.Label(fenster, text=T("Doppelklick spielt · Ziehen ordnet um · ⌘ zwei markieren, dann Tauschen · Esc hält an · ⌘O Karte, ⌘S Sicherungen, ⌘L Lücken"),
             bg=FARBE_HINTERGRUND, fg=FARBE_LEISE).pack(pady=(0, 10))

    # --- Ziehen und Ablegen: Reihenfolge ändern -------------------------
    def zieh_start(e):
        strich_weg()
        i = liste_index(e.y)
        # Nur echte Patch-Zeilen lassen sich ziehen: Hinweiszeilen (etwa
        # „N Patches ohne Ton …") haben keine Platznummer. Am 18.11.2026
        # führte das Ziehen einer solchen Zeile zu
        # „KeyError: None" in auto_scroll.
        zeile = zustand["zeilen"][i] if 0 <= i < len(zustand["zeilen"]) else None
        zustand["von"] = (i if zeile is not None
                          and zeile.get("nummer") is not None else None)
        # Auswahl: einfacher Klick wählt, ⌘ klickt dazu
        if zustand["von"] is not None:
            mehrfach = bool(e.state & 0x0008)      # Command-Taste
            liste_waehlen(zustand["von"], mehrfach=mehrfach)
            welle_laden()
            teile = zeile["text"].split()
            status_var.set("ziehen: %s" % (teile[1] if len(teile) > 1 else ""))
        zustand["strich"] = None
        if zustand["von"] is not None:
            liste.config(cursor="fleur")
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
            messagebox.showerror(T("Fehler"), str(f))

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
    # Kein Fokusklau beim Überfahren der Liste: das nahm dem Suchfeld den Fokus
    # (18.11.2026). Fokus kommt beim Klick — siehe fokus_zurueck.


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

    def fokus_halten(e=None):
        """Fokus an die Liste — aber niemals aus einem Eingabefeld heraus.

        Anlaß (18.11.2026): Die alte Regel „Fokus immer zur Liste" nahm dem
        Suchfeld den Fokus in dem Augenblick, in dem man hineinklickte — es
        war weiß und nahm nichts an. FocusIn kommt auch für das Fenster selbst,
        deshalb wird nach kurzer Wartezeit der TATSÄCHLICHE Fokus geprüft.
        """
        try:
            fenster.after(10, spaet_pruefen)
        except Exception:
            pass

    def spaet_pruefen():
        try:
            jetzt = fenster.focus_get()
            if jetzt is not None and jetzt.winfo_class() in (
                    "Entry", "Spinbox", "TEntry", "TCombobox", "Text"):
                return
            liste.focus_set()
        except Exception:
            pass

    # Klicks geben den Fokus an die Liste zurück — außer aus Eingabefeldern.
    fenster.bind("<ButtonRelease-1>", fokus_zurueck, add="+")
    fenster.bind("<FocusIn>", fokus_halten, add="+")
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
        try:
            einstellung_schreiben("fenster_geometrie", fenster.geometry())
            einstellung_schreiben("sortierung", sort_var.get())
        except Exception:
            pass
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
    fenster.title(T("JamMan Card Tool") + " — " + T("Karte und interner Speicher"))
    # Größe aus dem Bedarf der Bedienelemente - sonst werden die rechten
    # Knöpfe abgeschnitten (am 10.11.2026 gesehen: 1162 statt 1080 px).
    fenster.update_idletasks()
    bildschirm_b = fenster.winfo_screenwidth()
    bildschirm_h = fenster.winfo_screenheight()
    breite = min(max(1000, fenster.winfo_reqwidth() + 24), bildschirm_b - 40)
    hoehe = min(max(720, fenster.winfo_reqheight() + 24), bildschirm_h - 80)
    # Gemerkte Größe und Lage behalten (19.11.2026): Sie wurde oben gesetzt und
    # hier bisher überschrieben — das Merken der Fenstergröße war damit
    # wirkungslos. Behalten wird sie nur, wenn sie groß genug ist; sonst wächst
    # sie auf den Bedarf, damit nichts abgeschnitten wird.
    if _geo and "x" in _geo:
        import re as _re
        treffer = _re.match(r"^(\d+)x(\d+)([+-]-?\d+[+-]-?\d+)?$", _geo.strip())
        if treffer:
            b_alt, h_alt = int(treffer.group(1)), int(treffer.group(2))
            breite = min(max(b_alt, breite), bildschirm_b - 40)
            hoehe = min(max(h_alt, hoehe), bildschirm_h - 80)
            fenster.geometry("%dx%d%s" % (breite, hoehe, treffer.group(3) or ""))
        else:
            fenster.geometry("%dx%d" % (breite, hoehe))
    else:
        fenster.geometry("%dx%d" % (breite, hoehe))
    # Nachlauf (19.11.2026): Auf dem Prüfrechner von GitHub war das Fenster
    # französisch 1048 px breit, der Inhalt brauchte 1148 — die Messung oben
    # war vor dem letzten Layout-Durchgang entstanden. Deshalb bis zu drei
    # Mal nachmessen und nur wachsen, nie schrumpfen.
    for _ in range(3):
        fenster.update_idletasks()
        bedarf_b = min(fenster.winfo_reqwidth() + 24, bildschirm_b - 40)
        bedarf_h = min(fenster.winfo_reqheight() + 24, bildschirm_h - 80)
        if bedarf_b <= fenster.winfo_width() and bedarf_h <= fenster.winfo_height():
            break
        neu_b = max(fenster.winfo_width(), bedarf_b)
        neu_h = max(fenster.winfo_height(), bedarf_h)
        fenster.geometry("%dx%d" % (neu_b, neu_h))
        breite, hoehe = neu_b, neu_h
    # Reicht die Breite für die Knöpfe? Wenn nicht, um den fehlenden Rest
    # wachsen. Das wird ZWEIMAL geprüft: sofort und nach 200 ms — auf Linux
    # werden manche Häkchen erst nach dem ersten Zeichnen gemappt und waren
    # bei der ersten Messung noch nicht dabei (19.11.2026 im CI gesehen).
    def _breite_anpassen(*_a):
        nonlocal breite, hoehe
        try:
            fenster.update_idletasks()
            fehlt = [0]

            def _messen(w):
                if w.winfo_class() in ("Button", "Checkbutton", "Radiobutton",
                                       "Menubutton"):
                    try:
                        if w.winfo_ismapped():
                            fehlt[0] = max(fehlt[0],
                                           w.winfo_reqwidth() - w.winfo_width())
                    except Exception:
                        pass
                for kind in w.winfo_children():
                    _messen(kind)

            _messen(fenster)
            if fehlt[0] > 0:
                breite = min(fenster.winfo_width() + fehlt[0] + 8,
                             bildschirm_b - 40)
                fenster.geometry("%dx%d" % (breite, hoehe))
                fenster.update_idletasks()
        except Exception:
            pass

    _breite_anpassen()
    try:
        fenster.after(200, _breite_anpassen)
    except Exception:
        pass
    fenster.minsize(min(breite, 1000), min(hoehe, 700))

    # Selbsttest nach einem Update (19.11.2026): einmal, im Hintergrund, mit
    # demselben Bericht wie der Knopf. Läuft als eigener Prozeß, weil der
    # Selbsttest selbst Fenster baut und das im Hauptfaden laufen muß.
    def _selbsttest_nach_update():
        import subprocess as _sp
        import sys as _sys
        befehl = [_sys.executable, os.path.abspath(__file__), "funktionstest"]
        try:
            lauf = _sp.Popen(befehl, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
        except Exception:
            return

        def nachsehen():
            if lauf.poll() is None:
                fenster.after(500, nachsehen)
                return
            try:
                ausgabe = lauf.stdout.read() if lauf.stdout else ""
            except Exception:
                ausgabe = ""
            if ausgabe.strip():
                bericht_fenster(fenster, ausgabe)
        fenster.after(500, nachsehen)

    try:
        if (einstellung_lesen("selbsttest_nach_update") == "ja"
                and einstellung_lesen("selbsttest_noetig") == "1"):
            einstellung_schreiben("selbsttest_noetig", "nein")
            fenster.after(1500, _selbsttest_nach_update)
    except Exception:
        pass

    # Ohne Karte, aber mit Pedal am Kabel: gleich zeigen, was drin ist.
    if basis is None:
        def von_selbst_usb():
            try:
                if _greenglass().finde_anschluesse():
                    usb_oeffnen()
            except Exception:
                pass          # kein Pedal, kein USB-Modul: der Hinweis genügt
        fenster.after(400, von_selbst_usb)

    fenster.mainloop()


# -------------------------------------------------------------------- Start

def main():
    # Sprache VOR dem Bau des Parsers bestimmen - die Hilfetexte werden beim
    # Anlegen übersetzt, nicht beim Ausgeben.
    # Ohne --sprache gilt die gespeicherte Einstellung bzw. die Systemsprache.
    # Vorher wurde die Sprache erst NACH dem Bau des Parsers gesetzt - deshalb
    # war die Hilfe immer deutsch, auch bei JAMMAN_SPRACHE=en oder LANG=en_US
    # (Meldung #4, 22.11.2026).
    gewaehlt = None
    for i, arg in enumerate(sys.argv):
        if arg == "--sprache" and i + 1 < len(sys.argv):
            gewaehlt = sys.argv[i + 1]
        elif arg.startswith("--sprache="):
            gewaehlt = arg.split("=", 1)[1]
    sprache_setzen(gewaehlt or einstellung_lesen("sprache") or sprache_aus_system())
    p = argparse.ArgumentParser(
        description=T("JamMan Solo XT — Speicherkarte verwalten"),
        epilog=befehlsliste(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="JamMan Card Tool " + FASSUNG)
    # Nur für „funktionstest": alle fünf Sprachen prüfen (19.11.2026).
    p.add_argument("--alle-sprachen", action="store_true",
                   help=T("Funktionstest in allen fünf Sprachen"))
    p.add_argument("befehl", metavar=T("BEFEHL"),
                   choices=sorted(list(BEFEHLE) + list(BEFEHL_ALIAS)),
                   help=T("was zu tun ist (Liste unten; englische Namen gehen auch)"))
    p.add_argument("dateien", nargs="*", metavar=T("DATEIEN"),
                   help=T("Dateien für „speicherabbild“ und „vergleich“"))
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
    p.add_argument("--marker", type=float, action="append", metavar="SEK",
                   help=T("Schnittpunkt in Sekunden (mehrfach möglich)"))
    p.add_argument("--alle-pausen", action="store_true",
                   help=T("an jeder gefundenen Pause trennen"))
    p.add_argument("--schwelle", type=float, default=35.0, metavar="PROZENT",
                   help=T("Ruhepegel für die Pausenerkennung in Prozent (Vorgabe 35)"))
    p.add_argument("--min-pause", type=float, default=0.25, metavar="SEK",
                   help=T("kürzeste Pause, an der geschnitten wird (Vorgabe 0,25 s)"))
    p.add_argument("--fade-naht", type=float, default=0.0, metavar="MS",
                   help=T("Fade an jeder Naht in Millisekunden (Vorgabe 0)"))
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
    if a.befehl == "funktionstest":
        alle = getattr(a, "alle_sprachen", False)
        sys.exit(funktionstest(None if not alle
                               else ["de", "en", "fr", "es", "zh"]))
    if a.befehl == "selbsttest":
        selbsttest(a.karte)
        return
    if a.befehl == "menu":
        try:
            menu(a.karte)
        except KeyboardInterrupt:
            print()
        return
    if a.befehl == "aktualisieren":
        ok, text = aktualisieren(melden=lambda t: print("  " + t))
        print(text)
        return 0 if ok else 1
    if a.befehl in ("firmware-holen", "firmware-schreiben"):
        if not a.dateien:
            print(T("Aufruf:") + " jamman.py " + a.befehl + " <ordner>")
            return 2
        g = _geraet_holen(_greenglass())
        try:
            if a.befehl == "firmware-holen":
                geholt = firmware_holen(
                    g, a.dateien[0],
                    melden=lambda p: print("  " + str(p).split("nandfs/")[-1]))
                print(T("Firmware geholt: %d Dateien") % len(geholt))
            else:
                if not frage_ja_nein_konsole():
                    print(T("Abgebrochen."))
                    return 1
                ergebnis = firmware_schreiben(
                    g, a.dateien[0],
                    melden=lambda p, n: print("  " + str(p).split("nandfs/")[-1]
                                              + " (%d Bytes)" % n))
                print(T("Fertig: %d Dateien geschrieben") % len(ergebnis))
        finally:
            _geraet_schliessen()
        return 0

    if a.befehl in ("jamlists", "jamlist-holen", "jamlist-schreiben"):
        dateien = a.dateien or []
        if a.befehl == "jamlists":
            if not dateien:
                print(T("Aufruf:") + " jamman.py jamlists <JamManager.db.sqlite>")
                return 2
            for liste in jamlists_lesen(dateien[0]):
                print("  %-16s id=%-3d %d " % (liste["name"], liste["id"],
                                               len(liste["platz"]))
                      + T("belegte Plätze"))
            return 0
        if a.befehl == "jamlist-holen":
            if len(dateien) < 3:
                print(T("Aufruf:") + " jamman.py jamlist-holen <db> <Name> <ziel.txt>")
                return 2
            for liste in jamlists_lesen(dateien[0]):
                if liste["name"] == dateien[1]:
                    patchliste = [(("Patch%02d" % p), n, "") for p, n, _ in liste["platz"]]
                    _setlist_speichern(patchliste,
                                       list(range(len(patchliste))), dateien[2])
                    print(T("Geschrieben: %s (%d Plätze)")
                          % (dateien[2], len(patchliste)))
                    return 0
            print(T("Keine JamList mit dem Namen %s.") % dateien[1])
            return 1
        if len(dateien) < 3:
            print(T("Aufruf:") + " jamman.py jamlist-schreiben <db> <Name> <quelle.txt>")
            return 2
        eintraege = [(int(platz.lstrip("Patch") if platz.startswith("Patch")
                          else platz), name, "")
                     for platz, name in _setlist_lesen(dateien[2])]
        kennung = jamlist_schreiben(dateien[0], dateien[1], eintraege)
        print(T("JamList %s angelegt (id %d, %d Plätze)")
              % (dateien[1], kennung, len(eintraege)))
        return 0

    if a.befehl in ("speicherabbild", "vergleich"):
        if a.befehl == "vergleich":
            if len(a.dateien or []) < 2:
                print(T("Aufruf:") + " jamman.py vergleich <a.tsv> <b.tsv>")
                return 2
            nur_a, nur_b, anders = speicherabbilder_vergleichen(
                a.dateien[0], a.dateien[1])
            print(T("Nur im ersten Bild: %d") % len(nur_a))
            for pfad in nur_a[:20]:
                print("  + " + pfad)
            print(T("Nur im zweiten Bild: %d") % len(nur_b))
            for pfad in nur_b[:20]:
                print("  - " + pfad)
            print(T("Gleicher Pfad, andere Größe: %d") % len(anders))
            for pfad, groesse_a, groesse_b in anders[:40]:
                print("  %s  %d → %d" % (pfad, groesse_a, groesse_b))
            return 0
        ziel = (a.dateien or [None])[0]
        if not ziel:
            print(T("Aufruf:") + " jamman.py speicherabbild <datei.tsv>")
            return 2
        g = _geraet_holen(_greenglass())
        try:
            anzahl = speicherabbild(g, ziel,
                                    melden=lambda n, p: print("  %d  %s" % (n, p)))
            print(T("Speicherabbild geschrieben: %d Einträge nach %s")
                  % (anzahl, ziel))
        finally:
            _geraet_schliessen()
        return 0

    if a.befehl == "auswerfen":
        befehl_auswerfen(a.karte)
        return

    basis = karte_pruefen(a.karte)
    # Nur-Lesen-Modus (20.11.2026): Unbekanntes Gerät — lesende Befehle gehen,
    # schreibende nicht. Klare Meldung statt stiller Fehlschlag.
    if NUR_LESEN:
        lesend = ("liste", "struktur", "herausgeben", "wav_info",
                  "pruefen", "diagnose")
        if a.befehl not in lesend:
            raise SystemExit(
                T("Nur-Lesen-Modus: „%s“ ist ein unbekanntes Gerät (%s). "
                  "Der Kartenaufbau ist nicht belegt, deshalb wird nichts "
                  "geschrieben. Lesen ist erlaubt (liste). Mit "
                  "„jamman.py struktur <karte>“ entsteht ein Bericht, den Sie "
                  "weitergeben können.") % (a.karte, UNTERORDNER))
    if a.befehl == "liste":
        befehl_liste(basis)
    elif a.befehl == "herausgeben":
        ordner, anzahl, ohne = herausgeben(basis, a.ziel)
        print(T("Herausgegeben nach: %s") % ordner)
        print(T("%d Tonspuren als WAV, %d Plätze ohne Ton, dazu karte.csv "
                "und LIESMICH.txt.") % (anzahl, ohne))
    elif a.befehl == "struktur":
        ziel, anzahl = struktur_bericht(basis)
        print(T("Bericht geschrieben: %s") % ziel)
        print(T("Enthalten: Ordnernamen und die XML-Dateien, ohne Musik und ohne "
                "Ihre Texte (Patch-Namen sind durch … ersetzt). %d XML-Dateien.")
              % anzahl)
    elif a.befehl == "namensform":
        befehl_namensform(basis, a.schreiben, a.sicherung)
    elif a.befehl == "sichern":
        befehl_sichern(basis, a.ziel, a.schreiben)
    elif a.befehl == "aufspielen":
        if not a.wav:
            raise SystemExit(T("Fehler: --wav fehlt."))
        befehl_aufspielen(basis, a.wav, a.name, a.schreiben, a.stopp, a.rhythmus,
                          a.wiedergabe, a.fade, a.gain, a.normieren,
                          not a.ohne_begrenzung, a.trotzdem,
                          a.kuerzen, a.laenge)
    elif a.befehl == "abspielen":
        if not a.patch:
            raise SystemExit(T("Fehler: --patch fehlt."))
        befehl_abspielen(basis, a.patch)
    elif a.befehl == "loeschen":
        if not a.patch:
            raise SystemExit(T("Fehler: --patch fehlt."))
        befehl_loeschen(basis, a.patch, a.schreiben, a.sicherung)
    elif a.befehl == "teilen":
        if not a.wav:
            raise SystemExit(T("Fehler: --wav fehlt."))
        befehl_teilen(basis, a.wav, a.name or os.path.splitext(os.path.basename(a.wav))[0],
                      a.schreiben, a.teile, a.laenge, a.sicherung,
                      a.stopp, a.wiedergabe, sinnvoll=not a.einfach,
                      ohne_pause=not a.mit_pause, marker=a.marker,
                      schwelle=max(0.01, min(0.99, a.schwelle / 100.0)),
                      min_pause=a.min_pause, fade_naht=a.fade_naht,
                      alle_pausen=a.alle_pausen)
    elif a.befehl == "pegel-umkehren":
        if not a.protokoll:
            raise SystemExit(T("Fehler: --protokoll fehlt."))
        befehl_pegel_umkehren(basis, os.path.expanduser(a.protokoll),
                              a.schreiben, a.sicherung)
    elif a.befehl == "alle-normieren":
        befehl_alle_normieren(basis, a.schreiben, a.ziel_rms, a.grenze, a.sicherung)
    elif a.befehl == "zuruecknehmen":
        if a.alle:
            befehl_zuruecknehmen_alle(basis, a.aus, a.schreiben, a.sicherung)
        else:
            if not a.patch:
                raise SystemExit(T("Fehler: --patch fehlt (oder --alle angeben)."))
            befehl_zuruecknehmen(basis, a.patch, a.schreiben, a.sicherung, a.aus)
    elif a.befehl == "platzieren":
        if not a.patch or not a.auf:
            raise SystemExit(T("Fehler: --patch und --auf fehlen."))
        befehl_platzieren(basis, a.patch, a.auf, a.schreiben, a.sicherung)
    elif a.befehl == "tauschen":
        if not a.patch or not a.mit:
            raise SystemExit(T("Fehler: --patch und --mit fehlen."))
        befehl_tauschen(basis, a.patch, a.mit, a.schreiben, a.sicherung,
                        kopie=not a.keine_kopie)
    elif a.befehl == "aendern":
        if not a.patch:
            raise SystemExit(T("Fehler: --patch fehlt."))
        befehl_aendern(basis, a.patch, a.schreiben, a.stopp, a.rhythmus,
                       a.wiedergabe, a.name, a.sicherung, a.fade,
                       a.gain, a.normieren, not a.ohne_begrenzung)
    elif a.befehl == "saeubern":
        befehl_saeubern(basis, a.schreiben)
    elif a.befehl == "verschieben":
        if not a.patch or not a.nach:
            raise SystemExit(T("Fehler: --patch und --nach fehlen."))
        befehl_verschieben(basis, a.patch, a.nach, a.schreiben, a.sicherung,
                           kopie=not a.keine_kopie)


def _absturz_protokoll(fehler):
    """Unerwarteten Fehler festhalten und zeigen.

    Nötig, weil das Werkzeug unter Windows ohne Konsolenfenster startet
    (pythonw): Ohne das Protokoll wäre ein Absturz unsichtbar (17.11.2026).
    """
    import traceback
    # Ältere Pythons (Apples /usr/bin/python3 ist 3.9) kennen die
    # Ein-Argument-Form von format_exception nicht. Anlaß (19.11.2026): Der
    # Absturzbericht selbst stürzte ab, und der eigentliche Fehler blieb
    # unsichtbar.
    try:
        text = "".join(traceback.format_exception(
            type(fehler), fehler, fehler.__traceback__))
    except Exception:
        text = "%s: %s" % (type(fehler).__name__, fehler)
    ordner = os.path.dirname(os.path.abspath(__file__))
    pfad = os.path.join(ordner, "jamman-fehler.log")
    try:
        with open(pfad, "a", encoding="utf-8") as datei:
            datei.write("\n" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            datei.write(text)
    except OSError:
        pfad = ""
    try:
        from tkinter import messagebox
        messagebox.showerror(
            T("Unerwarteter Fehler"),
            (T("Das Werkzeug wurde unerwartet beendet.") + "\n\n"
             + text[-1200:]
             + ("\n\n" + T("Protokoll:") + " " + pfad if pfad else "")))
    except Exception:
        pass
    return pfad


if __name__ == "__main__":
    # Liegt das Paket noch flach (Fassung 3.120 oder älter), wird es beim
    # ersten Start umgebaut und danach neu gestartet (19.11.2026).
    try:
        _rechte_pruefen()
    except Exception:
        pass
    try:
        if _aufteilung_migrieren():
            import subprocess as _sp
            neuer = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 ".jamman", "jamman.py")
            print(T("  Ordner aufgeräumt — das Werkzeug startet neu …"))
            _sp.Popen([sys.executable, neuer, *sys.argv[1:]], cwd=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            sys.exit(0)
    except Exception:
        pass
    try:
        main()
    except SystemExit:
        raise
    except BaseException as fehler:        # auch Strg+C beim Start
        _absturz_protokoll(fehler)
        raise
