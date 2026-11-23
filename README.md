<p align="center">
  <img src="jamman.png" alt="JamMan Card Tool" width="128">
</p>

# JamMan Card Tool — memory card of the JamMan Solo XT, Stereo and Delay

![The window](.jamman/docs/fenster-englisch.png)


For the card and the internal memory — over USB as well.

**Manage the SD card of a DigiTech JamMan Solo XT, Stereo or Delay from macOS,
Windows or Linux — without JamManager XT.**


**The way out is built in:** "Export everything …" turns the card into ordinary
WAV files plus a list — readable without this tool. Whoever stops, migrates or
passes a pedal on takes the music with them.

*Deutsche Fassung: [README.de.md](README.de.md) · Seite mit Bildern:
https://www.julius-k.de/tool/en/*

Cards from the **JamMan Stereo** and the **JamMan Delay** are recognised by their
device folders, `JamManStereo` and `JamManDelay`. **For the Delay the layout is
confirmed on a real pedal** (Plywood1107, 20 November 2026): the folder is
`JamManDelay`, below it `PatchNN/patch.xml` and `PatchNN/PhraseA/phrase.xml`, and
the XML fields are the same as on the Solo XT and the Stereo — `PatchName`,
`RhythmType`, `StopMode`, `SettingsVersion`, `ID`, `Metadata`, plus
`BeatsPerMinute`, `BeatsPerMeasure`, `IsLoop`, `IsReversed`, `AudioVersion`. The
one difference: the root element carries `device="JamManDelay"`, and the tool
writes it that way.
**Not confirmed** is the name of the audio file inside `PhraseA` — the XML was
shown, the WAV was not. So: copy the card before writing to it for the first
time. Stereo support was tested with a rebuilt card and, since
13 November 2026, **confirmed by a user on a real JamMan Stereo (Kubuntu)** —
copy the card before writing to it for the first time.

**If the tool ever stops working.** A card should not hold anyone hostage:
`jamman.py herausgeben --karte <path> --ziel <folder>` (or in the window "Manage
backups …" → "Export everything …") copies every track as an ordinary WAV file,
named after its slot and patch name, plus `karte.csv` with slot, name, length,
stop mode, playback, tempo and rhythm and a `LIESMICH.txt`. Those files do not
need this tool — they work in any other program and on any other device. The card
itself is only read.

**What the tool does not need:** no administrator password, no installation, no
notarisation, no network access beyond the update check. It is a Python script
that reads and writes the card — open for inspection, because the source is (MIT).

**If macOS blocks the starter:** for `JamMan-macOS.command` macOS may say the file
is "from the internet" and unsigned. Then once in Terminal:

```bash
xattr -d com.apple.quarantine "…/JamMan-macOS.command"
chmod +x "…/JamMan-macOS.command"
```

After that a double click works. If you prefer not to, run the script directly:
`python3 .jamman/jamman.py` (Python with Tk 8.6 or newer, see below).

The official DigiTech *JamManager XT* (version 2.0, 2012) crashes on recent
macOS releases as soon as the pedal is connected:

```
Tcl_Panic -> TkpInit -> _tkinter -> Tk()   ->  SIGABRT
```

DigiTech does not maintain the software any more. This tool avoids it
completely: it works **directly on the SD card**. For that the microSD has
to come out of the pedal and go into a card reader or slot — with the USB
cable attached, the pedal does not show up as a drive on current macOS
versions.

Everything is plain Python 3 with the standard library only.
No ffmpeg, no Homebrew, no build step.

---

**Test it yourself:** if a button does nothing or a window stays open, you can
check the tool on your own machine. It creates a test card in the temp folder,
presses every button and runs the file commands — no pedal, no internet:

```
JamMan-macOS.command funktionstest         # macOS
JamMan-Windows.bat funktionstest           # Windows
./JamMan-Linux.sh funktionstest            # Linux
```

`--alle-sprachen` runs it in all five languages. The result is "keine
Befunde" (nothing found) or a list; the findings also go to
`jamman-fehler.log` so they can be sent in.


## New versions

When the window opens, the tool asks GitHub **once a day** for the latest
release. If it is newer than the running copy, a hint appears offering to open
the download page. The request runs in the background — without a network the
window opens just as fast and nothing is reported.

All that is sent is the request to `api.github.com`: no card, no file names,
no user name.

To switch it off, put

```
update_pruefung=nein
```

into the settings file (its path is printed by `jamman.py selbsttest`).

## What it does

| Command | Effect |
|---|---|
| `list` | list the patches on the card: name, length, WAV format |
| `play --patch N` | play a patch |
| `import --wav FILE` | add a file as a new patch — WAV, AIFF, CAF, MP3, M4A/AAC, FLAC, OGG/Opus; converts to 44.1 kHz / 16 bit / stereo |
| `change --patch N` | change stop mode, loop/single, rhythm or name of an existing patch |
| `move --patch N --nach M` | move a patch to another slot |
| `delete --patch N` | remove a patch (backup first) |
| `backup --ziel DIR` | copy the whole card to the computer |
| `clean` | remove macOS `._*` and `.DS_Store` files from the card |
| `eject` | unmount / eject the card |
| `gui` | small window (tkinter) |
| `menu` | text menu — works without tkinter |

**Command names work in English and German** (`list` = `liste`, `import` =
`aufspielen`, `backup` = `sichern` …). The **options are German** — they are
older than the translation and were never renamed:

| Option | Meaning |
|---|---|
| `--karte PFAD` | the card (default: detected) |
| `--patch N` | patch number |
| `--wav DATEI` | the audio file to import |
| `--ziel ORDNER` | target folder for `backup` |
| `--nach N` | target slot for `move` |
| `--schreiben` | really write (without it: dry run) |

**Dry run by default.** Nothing is written unless you add `--schreiben`.

**Backup before every write.** Each writing command copies the affected
patch folders to `~/JamMan-Sicherung` first.

---

## Over USB

The pedal does not appear as a drive. It speaks a serial protocol
("Greenglass", CDC-ACM), and the tool talks to it directly — no driver, no
JamManager. **That also means the computer does not have to recognise the pedal
as a drive.** A card reader is still the faster route (about 1.3 MB/s over USB);
without one, the tool gets you there anyway. Button **"Pedal über USB"** opens a window for the pedal's
**internal memory** and its **SD card**:

* list the patches with their names, switch between internal memory and card
* fetch everything into the main window (descriptions first, audio after)
* write a whole patch folder onto the pedal, slot by slot
* write a whole **fetched folder back at once** ("Schreiben ▾ → Ganzen
  Ordner aufs Pedal …") — occupied slots are backed up, unchanged ones
  skipped
* **delete a slot** ("Schreiben ▾ → Platz löschen …")
* write an audio file as a new patch (any format, converted to 44.1 kHz /
  16 bit / stereo)
* play a loop **backwards** (the pedal reverses it; the window previews it
  backwards too when ffmpeg is available)
* see and set the **tempo** per patch (column Tempo, „Beschreibung …“)
* back everything up as a ZIP, clean up old backups
* save and fetch setlists
* read **JamLists from JamManager XT** and store your own setlists as a JamList
  (the manufacturer's library is an SQLite file, not a special format)
* **search and sort** in the list (folder, name, duration, reversed name)
* play a loop **backwards** and see/set the **tempo** per patch
* **delete a slot** and **close the gaps**
* take a **memory image** of the pedal and compare two of them (read only)
* undo the last write

Anything that is overwritten is first copied to the computer; each slot keeps
its last five backups (`vor-ueberschreiben/<slot>/<stamp>`). Files longer than
10 minutes are refused — the pedal does not accept them. Speed over USB
is about 1.3 MB/s — an 8-minute loop takes around a minute. For the card in
a card reader, use the reader instead; it is many times faster.

On Windows, USB needs `pyserial` (`pip install pyserial`); macOS and Linux
need nothing extra. On Ubuntu, USB access to the internal memory was
confirmed on 17 Nov 2026.

**Linux: `dialout` group — and reboot afterwards.** If the pedal is not
found, the user is usually not in the `dialout` group:
`sudo usermod -aG dialout $USER`. Logging out and back in is not always
enough — a user on Kubuntu needed a **reboot** on 18 Nov 2026 before it
worked. If `groups` does not list `dialout`, the session is still the old one.

Windows 10 (22H2, build 19045) shows the pedal as "USB Serial Device (COMx)" —
confirmed on 17 Nov 2026. **Reading and writing over USB were tested on Windows on
23 Nov 2026:** one second of silence written to a free slot, seen in the list (0:01) and
deleted again. This needs `pyserial` (`pip install pyserial`); without it the tool reports
"no pedal found" even though Windows knows the port.
**The package contains `pyserial-nachinstallieren.bat` for this:** double-click it, it
installs `pyserial`, says "done" and waits for a key press. Online copy:
<https://www.julius-k.de/tool/pyserial-nachinstallieren.bat>.

**Updating from 3.117 or older:** the built-in update cannot install the new
layout (it looks for a flat `jamman.py` and skips every path starting with a dot
when extracting). Download once by hand:
https://github.com/Suilui1/jamman-card-tool/releases/latest — unpack it, put the
old folder aside. From 3.118 on the tool updates itself again.

## Starting it

**The shortest way is the shortcut on the desktop:**

| System | What you click |
|---|---|
| macOS | `JamMan Card Tool.app` (desktop, Dock or `/Applications`) |
| Windows | `JamMan Card Tool` — the shortcut on the desktop |
| Linux | `jamman-card-tool` — desktop or application menu |

If it is missing, a double-click creates it: `Verknuepfung-macOS.command`,
`Verknuepfung-Windows.bat` or `Verknuepfung-Linux.sh`. And without a shortcut
the three starters in the folder always work.

**On the first start on macOS** macOS may say the developer cannot be verified —
the bundle is not signed. Then right-click it → **Open** → **Open** again.
After that it starts normally.

The package keeps only a few things in plain sight: the three starters (one per
system), this guide, the licence and two folders.

`Hilfe-Help-Aide-Ayuda-帮助/` carries its name in every language the tool
speaks — a reader of Chinese or Spanish recognises it without guessing. It holds
the extra starters (console window, text menu), the old short file names as
forwarders and the text files.

`.jamman/` is hidden on purpose: that is the program itself. There is no need to
look inside.
**macOS:** double-click `JamMan-macOS.command`. macOS opens a Terminal for
`.command` files, and **the window closes together with the tool** when you
quit. The first time macOS asks whether the script may control Terminal —
allow it once, and that is the end of it.

If you would rather not allow that, the same effect is a setting: Terminal →
Settings → Profiles → "Shell" → "When the shell exits: Close the window".

**With iTerm** the script closes nothing (iTerm cannot be scripted without a
permission prompt); that is what the setting is for: iTerm → Settings →
Profiles → General → "When the session ends: Close" — on by default for the
"Default" profile, so the window closes on its own.

**Windows:** `JamMan-Windows.bat` (no console window) or `JamMan-Windows-Konsole.bat` if
you want messages to stay visible.

**macOS:** double-click `JamMan-macOS.command`. If macOS says it “could not be
executed because you do not have the necessary access privileges”, the execute
bit is missing — this happens when the file was copied via an exFAT stick, a
Windows share or an archive that does not keep permissions:

```
chmod +x JamMan-macOS.command     # once, in Terminal, inside that folder
```

`bash JamMan-macOS.command` also works without the execute bit. Since 3.122
the tool repairs the permission on every start — but only once it runs.

**Linux:** go into the folder and start it:

```
cd jamman-card-tool-3.122        # the name of the unpacked folder
chmod +x JamMan-Linux.sh
./JamMan-Linux.sh
```

If `chmod` answers "command not found", the terminal is not in that folder —
`cd` first, then `ls` (it must list `JamMan-Linux.sh`). If it says "permission
denied", the `chmod` was missing. Without the execute bit
`bash JamMan-Linux.sh` also works.

## Shortcut on your desktop

So the tool does not have to be hunted down in the file manager every time, a
small starter creates a shortcut with its own icon:

| System | File | creates |
|---|---|---|
| Windows | `Verknuepfung-Windows.bat` | “JamMan Card Tool” on the desktop |
| macOS | `Verknuepfung-macOS.command` | “JamMan Card Tool.app” on the desktop (with icon) |
| Linux | `Verknuepfung-Linux.sh` | entry in the application menu and on the desktop |

**On macOS and Linux the starter is already in the package:** the ready bundle
`JamMan Card Tool.app` (put next to the program files or moved to
`/Applications` — it finds the tool folder by itself) and the entry
`JamMan Card Tool.desktop`. On Windows the start goes through `wscript` and
`starte-jamman.vbs`, so no console window flashes up. On macOS the bundle can be dragged into the Dock or
moved to `/Applications`; it starts `JamMan-macOS.command`.

**Ubuntu and other GNOME systems** only launch a `.desktop` file once it is
marked executable: right-click it on the desktop and choose “Allow Launching”.
Running `Verknuepfung-Linux.sh` once gives you entry, icon and permissions in a
single go.

If the tool folder is moved later, the shortcut has to be created once more —
the path is baked into it.

## Firmware from one pedal to another

The pedal has no firmware server any more: the address JamManager XT used to
update from (`digitech.com/updater/…`) is gone. If you own two pedals with
different firmware, the newer one can be copied onto the older one — the tool
can do that.

**What the firmware actually is.** Not a secret package but files on a QNX
system inside the pedal:

```
/mnt/nandfs/usr/IOMgr        I/O manager
/mnt/nandfs/usr/JamManUI     user interface
/mnt/nandfs/usr/loopengine   loop engine
/mnt/nandfs/usr/usbserial    USB serial driver
/mnt/nandfs/usr/attributes/  button assignment and queue (newer versions only)
/mnt/nandfs/lib/             devb-sd-at91sam9g35 · devc-serusart
                             devc-serusb_g · devu-g15-at91sam_g.so
```

**Procedure.**

```
1  On the NEWER pedal: "Sichern ▾ → Firmware dieses Pedals holen …"
   → choose a folder; the eight files are stored below it (…/mnt/nandfs/…)
2  On the OLDER pedal: "Schreiben ▾ → Firmware übernehmen …"
   → choose the same folder
3  First the tool backs up ALL files of the target into
   sicherung-vor-firmware-<stamp>/
4  Then it writes them and checks the size of each one
5  Switch the pedal off and on again; the version appears at start-up
```

**What deliberately does not happen:**

```
unit_guid.attr        stays on the target — it is that pedal's own id
JamManSoloXT.xml      untouched — your settings live there
.filetable            not copied — the filesystem index belongs to the pedal
```

**Why "write beside it":** QNX refuses to overwrite a file that is running
("Resource busy"), so the tool writes `…/JamManUI.neu` and renames it over the
old one.

**The risk, plainly.** An interruption can make the pedal unusable; the only
way back is the backup on the computer. On 18 Nov 2026 a Solo XT was moved from
FirmwareVersion 5376 to 5632 (display showed "1.06") — it worked, but it
remains surgery on system files.

## macOS: “JamMan-macOS.command” was not opened

When the ZIP is downloaded, macOS marks every extracted program (quarantine).
The first start then shows:

> “JamMan-macOS.command” was not opened — Apple could not verify that
> “JamMan-macOS.command” is free of malware.

That is not a fault of the tool but the default for anything not registered
with Apple. Two ways:

**Allow it once:** right-click (or Ctrl-click) `JamMan-macOS.command` in the Finder →
**Open** → **Open** again in the dialog. After that a double click works.

**Or remove the mark** (Terminal, adjust the path):

```
xattr -dr com.apple.quarantine /path/to/jamman-card-tool
```

## Card layout

**Who proved what?** This table is the record, and it only grows when evidence
arrives — not when something sounds plausible.

| Device | Device folder | Evidence | Layout | Open |
|---|---|---|---|---|
| JamMan Solo XT | `JamManSoloXT` | own device, in daily use | confirmed | — |
| JamMan Stereo | `JamManStereo` | a user on his own pedal, Kubuntu, 13 Nov 2026 ("Funktioniert, top.") | confirmed | other systems untested |
| JamMan Delay | `JamManDelay` | Plywood1107: `patch.xml`/`phrase.xml` from the pedal (20 Nov 2026) and the folder listing over USB (23 Nov 2026) — 13 patches, `Patch01/PhraseA/phrase.wav` | confirmed | — |
| JamMan Solo HD | unknown | — | **open** | the device opens read-only; "Report structure …" delivers the evidence |

**The evidence itself sits in `Belege/` in the repository** — with date, source
and what it explicitly does **not** prove.

Verified on real hardware (2026-10-26):

```
JamManSoloXT/
    Patch01/
        patch.xml          <PatchName>, <RhythmType>, <StopMode>, <ID>
        PhraseA/
            phrase.wav     44.1 kHz, 16 bit, stereo, PCM
            phrase.xml     <IsLoop> (single/loop), tempo, <ID>
    Patch02/
    ...
```

The slot is defined by the folder name: the pedal shows `Patch07` as
patch 7. Names belong to the device — the tool never invents new ones.

Two stop modes exist on the card: `FadeOut` and `StopInstantly`.
Two rhythm types: `Silence` and `StudioKickAndHighHat`
(the latter pairs with `<SettingsVersion>1</SettingsVersion>`).

---

## Download

```
git clone https://github.com/Suilui1/jamman-card-tool.git
```

Or use "Code" → "Download ZIP" on the project page.

## Requirements

* **Python 3** with tkinter (only for the window; the `menu` command needs no tkinter)

**Runs on Intel and on ARM (Apple Silicon).** The tool is plain Python with no compiled
parts: the same on an Intel Mac (x86_64) as on an M1 through M4 (arm64). The
bundled `JamMan Card Tool.app` is just a start script with an icon — there is no
processor dependency, nothing to choose, no second build.

| | supported |
|---|---|
| macOS | Intel and Apple Silicon, from macOS 10.13 (bundle); Python 3 with Tk 8.6 |
| Windows | Windows 10 and 11, 64-bit; Python 3 with Tk 8.6 |
| Linux | x86_64 and arm64; `python3-tk` |

**Python 3.8 or newer is enough** — measured with `vermin` over the source (22 Nov
2026), not guessed. Tested on macOS 10.13 and newer, and on Windows 10 and 11
(64-bit). **Older Windows versions are untested:** Windows 8.1 and Windows 7 would
need Python 3.8, the last one that runs there. On very old systems the path length
(260 characters) and the console character set can get in the way.

**If something is missing, the tool says so:** without Python the Windows starter
shows the download address; without Tk the tool falls back to the text menu
instead of staying silent.

| System | Python | Start |
|---|---|---|
| macOS | `xcode-select --install`, or Python from [python.org](https://www.python.org/downloads/) | `JamMan-macOS.command` |
| Windows | Python from [python.org](https://www.python.org/downloads/) ("Add python.exe to PATH") | `JamMan-Windows.bat` |
| Linux | `sudo apt install python3-tk` | `./JamMan-Linux.sh` |

**Updating:** the button "Update" in the window fetches the latest version from GitHub, verifies it and replaces the program files. The old files go to `sicherung-vor-<old version>/` first; the settings in `~/.jamman-werkzeug.cnf` are not touched. From the command line: `jamman.py aktualisieren`.


**Automatically:** once a day the tool asks GitHub, and if there is a newer version it installs it without asking, then says to restart. The checkbox “Update beim Start” turns that off and it asks as before.
**Over USB on Windows:** the pedal appears as a serial device
("USB Serial Device (COMx)") — no DigiTech driver needed. The tool talks to
it through `pyserial`:

```
pip install pyserial
```

Check the connection in Device Manager under "Ports (COM & LPT)"; if there is
no COM port, the pedal is not enumerated as a serial device (cable, hub or
the device itself).

Audio playback uses what the system has: `afplay` (macOS), `winsound`
(Windows), `aplay`/`paplay`/`mpv`/`ffplay` (Linux).

**MP3 and M4A on Windows:** the pedal only takes WAV. Windows needs
`ffmpeg` for other formats:

```
winget install --id Gyan.FFmpeg
```

or drop `ffmpeg.exe` next to `jamman.py`. Plain WAV works without ffmpeg.

**Finding the card:** macOS `/Volumes/*`, Windows every drive letter,
Linux `/media/<user>/*`, `/run/media/<user>/*`, `/mnt/*`.

**What is tested and what is not:**

| System | State |
|---|---|
| macOS (Apple Silicon) | fully tested, in daily use |
| macOS (Intel) | same code, same Python — expected to work, **not** tested |
| Windows | audio via `winsound`, card search via drive letters — **not** tested on a Windows machine |
| Linux | audio via `aplay`/`paplay`, card search via `/media` — **not** tested on a Linux machine |
| Android | **not possible**: no tkinter, and Android cannot mount the card writable. That would be a new app, not a port. |

`jamman.py selbsttest` prints the system, the audio program, the card
search roots and the font it would use.

---

## Command names

Every command exists in German and in English — the English alias is
listed second:

| Task | Commands |
|---|---|
| list patches | `liste` · `list` |
| back up the card | `sichern` · `backup` |
| import a file | `aufspielen` · `import` |
| play a patch | `abspielen` · `play` |
| delete a patch | `loeschen` · `delete` |
| move / swap / place | `verschieben` · `move`, `tauschen` · `swap`, `platzieren` · `place` |
| restore a backup | `zuruecknehmen` · `restore` |
| normalise all | `alle-normieren` · `normalize` |
| undo a level change | `pegel-umkehren` · `undo-level` |
| split a file | `teilen` · `split` |
| change a patch | `aendern` · `change` |
| clean clutter | `saeubern` · `clean` |
| eject the card | `auswerfen` · `eject` |
| normalise folder names | `namensform` · `names` |
| window / text menu | `gui` · `menu` |
| self test | `selbsttest` · `selftest` |

`--help` follows the chosen language.

## Usage

```bash
# card inserted, e.g. /Volumes/JAMMAN or D:\ or /media/<user>/JAMMAN
python3 jamman.py liste

# back up the whole card first — always a good idea
python3 jamman.py sichern --ziel ~/JamMan-Sicherung --schreiben

# add a file as a new patch
python3 jamman.py aufspielen --wav myloop.wav --name "My Loop" \
        --stopp fade --wiedergabe loop --schreiben

# change an existing patch
python3 jamman.py aendern --patch 7 --stopp fade --wiedergabe loop --schreiben

# move / delete
python3 jamman.py verschieben --patch 12 --nach 3 --schreiben
python3 jamman.py loeschen --patch 12 --schreiben

# done
python3 jamman.py auswerfen
```

The card is found automatically. To point at a specific path use
`--karte /path/to/card`.

---

## Working around the 10-minute limit: splitting a file

**The pedal accepts at most 10 minutes per loop** (manual page 60,
stereo). Longer files do land on the card, but the pedal does not show
them — they can even disappear again.

The manual is here:
<https://eadn-wc05-7545739.nxedge.io/wp-content/uploads/2022/09/DigiTech_JamMan_Solo_XT_Manual.pdf>
(9 MB, DigiTech). The same source gives the other limits: 44.1 kHz,
16 bit, stereo, at most 200 loops, microSDHC up to 32 GB.

The tool therefore refuses a file that is too long and offers three ways
out:

| Way | Option | Result |
|---|---|---|
| shorten | `--kuerzen` | one file cut to 9:59 (last 12 ms faded out) |
| cut to length | `--laenge 540` | one file of at most 540 s |
| **split** | `--teile 2` | **two patches, each below the limit** |

### How splitting works

```
python3 jamman.py split --wav recording.wav --name "Piece" --teile 2
python3 jamman.py split --wav recording.wav --name "Piece" --laenge 540
```

- `--teile N` makes N roughly equal parts, `--laenge S` parts of at most
  S seconds.
- Cuts are placed **at pauses** when there are any (within ±90 s of the
  calculated point). `--einfach` cuts evenly instead.
- The seam is **sample-exact**: no sample is lost, none is doubled.
- The pause at the seam is **removed**, so part 1 ends where the sound
  stops and part 2 begins where it resumes. `--mit-pause` keeps it.
- The parts are named `Piece 1/2`, `Piece 2/2` … and take the next free
  slots. **Each part must stay below 10 minutes on its own** — the tool
  checks that and shortens if needed.

In the window this is the **"Split file"** button with the field "Parts"
and the tick box "cut at pauses".

### Playing the parts back to back on the pedal

The parts are ordinary patches. For a gapless join the pedal switches with
the **FS3X footswitch**: cue the next loop **at least 2 seconds before the
end** (manual page 57), then it continues without a gap. Without an FS3X
you press UP/DOWN by hand — that leaves a short pause.

The tool does the same for checking: the tick box "continue automatically"
or the "cue" button plays the parts without a gap (version 3.27 and later).

## Status

| System | Status |
|---|---|
| macOS 27, Apple Silicon | tested, all functions |
| macOS 15, Intel (MacBook Pro 2012) | tested, all functions |
| Windows 10 | tested by the author (window and command line) |
| Linux (Ubuntu Studio) | tested by the author |
| Linux (Kubuntu) | confirmed by a user, **JamMan Stereo** |

"Tested" means: the window opened, a card was read, a patch was written and
read back on the pedal. For the **Stereo** there is one report so far — from
the user on Kubuntu. Nobody has reported a Stereo card on Windows yet.

On some Intel Macs Apple's bundled Python 3.9 ships with Tk 8.5.9, which
aborts when opening a window. `JamMan-macOS.command` detects this and falls
back to the text menu; installing Python from python.org (Tk 8.6) brings
the window back.

---

## Safety

This tool writes to a memory card that a device depends on.

* It always backs up before writing — but **make your own copy of the
  `JamManSoloXT` folder before the first write**, no matter what.
* After every change: eject the card, put it into the pedal, and look.
  What the device actually does is not written in any XML file.
* Test on one patch before changing a whole card.
* No warranty. See `LICENSE`.

Not affiliated with DigiTech, Harman or Samsung. The names are used only
to describe what this is for.

---

**The evidence sits in `Belege/` in the repository** (not in the package) — the Delay's XML structure and the Stereo confirmation, with date and source.

## Languages

**Translation fixes are welcome** — French, Spanish and Chinese are mine, not a
native speaker's. One sentence with a better wording is enough; I put it in and
credit it.

German, English, French, Spanish and Chinese. Three ways to pick one:

| Way | Scope |
|---|---|
| in the window: "Sprache / Language" (top right) | permanent (`~/.jamman-werkzeug.cnf`) |
| `--sprache en` on the command line | this run only |
| no choice | system language from `LANG`/`LC_ALL`, else German |

**About the translations:** German is the source, English is complete and
checked. French, Spanish and Chinese are translated but **not reviewed by
native speakers** — the window shows a note for those languages.
Corrections are welcome: the texts are a dictionary near the top of
`jamman.py` (`TEXTE`, keyed by the German sentence).

A missing entry falls back to English, then to German, so a half-finished
translation never leaves empty labels.

## Support

The tool is free. If you find it useful you may contribute something:

**Donate:** [paypal.com/donate … PEJEFMSKUPA8C](https://www.paypal.com/donate/?hosted_button_id=PEJEFMSKUPA8C&currency_code=EUR)

The "Donate" button in the window opens the same page. The identifier is
public; no account details are in it.

## License

MIT — see [LICENSE](LICENSE).

German documentation: [README.de.md](README.de.md).

## Questions and answers

**Card in a reader, or the pedal over USB?** Both. Over USB the **internal memory**
and writing are available as well; about 1.3 MB/s flows there, so a card reader is
faster. For one full backup the reader is the better choice.

**Which Python do I need?** Python 3 with **Tk 8.6 or newer**. On macOS that means
the python.org build; the built-in `/usr/bin/python3` has Tk 8.5 — the window will
not draw, and the tool falls back to its text menu.

**Why copy the card before the first write?** Because a backup is cheaper than a
broken card — even when the layout is on record, as it is for the Solo XT, the Stereo
and the Delay (own card, and issue #2 for the other two; see the table above), but not
yet for the Solo HD. Since 1.x the tool backs up before every write — the extra copy by
hand is belt and braces.

**macOS says the starter is "from the internet"?** Once in Terminal:

```bash
xattr -d com.apple.quarantine "…/JamMan-macOS.command"
chmod +x "…/JamMan-macOS.command"
```

**Can I go back to an older version?** Yes, but not every old release is public any
more (the repository was tidied up). If you need a specific one: ask, and it will be
provided again. The newest one is under `releases/latest`.

**What does it cost?** Nothing. MIT licence, no account, no ads. If you feel like
it, the "Donate" button is there — nobody has to.

**Which languages?** German, English, French, Spanish, Chinese — interface,
messages and documentation.

## If something is wrong

1. **Press the self test** ("Self test …" in the window). It checks the tool on your
   own computer, without a pedal, and writes a report.
2. **Note the version** — top right in the window, for example `3.135 (20.11.2026)`.
3. **Send the log:** `jamman-fehler.log` sits next to the program file
   (`.jamman/jamman-fehler.log`). It holds the messages of the last runs.
4. **Name your system** (macOS 27.3, Windows 11, Kubuntu 24.04 …). With version and
   system almost anything can be reproduced.

## What stays on your computer

```
~/.jamman-werkzeug.cnf        settings (language, sorting, check boxes)
~/JamMan-Sicherung/           backups the tool creates itself
~/JamMan-Herausgabe/          folders from "Export everything …"
.jamman/jamman-fehler.log     log; may be deleted at any time
```

**None of it lives in the system: no daemon, no autostart.** To clean up: delete the
settings and the log, and look at the backup and export folders first — that is
where the music is. Deleting these files does **not** touch the card itself.

## What users say

> "Funktioniert, top." — JamMan Stereo owner on Kubuntu, 13 November 2026

> "It is awesome that you were able to build a tool that solves this for yourself
> and other owners!" — DigiTech Support, 19 November 2026

**Both are real replies with a date; none of it is paid for.**
