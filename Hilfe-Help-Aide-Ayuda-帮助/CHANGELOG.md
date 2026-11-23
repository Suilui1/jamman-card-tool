# Changelog

## 3.136.16 — 2026-11-23

### Geändert

- **Der USB-Weg braucht kein Laufwerk — das steht jetzt da.** Andere Werkzeuge für dieselben
  Pedale schreiben nur die Speicherkarte, weil der Laufwerks-Modus auf neueren Systemen nicht
  mehr zuverlässig arbeitet. Dieses Werkzeug spricht das Pedal über die serielle Schnittstelle
  an (Greenglass, CDC-ACM) und ist davon unabhängig. Der Satz steht in beiden READMEs und auf
  beiden Werkzeugseiten, samt GitHub-Pages-Fassung. Sonst keine Änderung am Programm.

## 3.136.15 — 2026-11-23

### Behoben

- **Das englische README sagte beim Stereo und beim Delay weiter „assumed", während die
  Tabelle „confirmed" führte.** Neue Prüfung `werkzeuge/belegt_pruefen.py`: sie liest
  beide READMEs, vergleicht Tabelle und Fließtext und verlangt, daß Deutsch und Englisch
  denselben Stand nennen. Gefunden hat sie den Widerspruch in der englischen Fassung,
  nachdem die deutsche schon berichtigt war — das ist der Beweis, daß die Prüfung etwas
  sieht.

## 3.136.14 — 2026-11-23

### Geändert

- **Der Aufbau der Delay-Karte ist vollständig belegt.** Plywood1107 hat in Issue #2 die
  Ordnerliste seiner Karte über USB geschickt: `JamManDelay/` mit `Patch01 … Patch13`,
  darunter `PhraseA/` und `patch.xml`, in `PhraseA` wiederum `phrase.wav` und
  `phrase.xml`. Damit ist auch der letzte offene Punkt geschlossen — der Name der
  Audiodatei. Beide READMEs sagen das jetzt so, statt „angenommen".
## 3.136.13 — 2026-11-23

### Geändert

- **Die Fensterbilder zeigen keine echten Patch-Namen mehr.** Die bisherigen Aufnahmen
  waren mit einer echten Speicherkarte entstanden; auf ihnen standen Patch- und
  Projektnamen sowie der Laufwerkspfad des Verfassers. Die neuen Aufnahmen stammen von
  einer Demo-Karte mit „Example 01 … 12".

## 3.136.12 — 2026-11-22

* **The guide now says “ARM”, not only “Apple Silicon”.** “Apple Silicon” is the
  marketing name; anyone looking for “ARM” found nothing — as happened when the
  hint was searched for. The sentence and the table now read “Intel and ARM
  (Apple Silicon)”; the table already listed `arm64` for Linux.
* Documentation only — the program is unchanged from 3.136.11. 3.136.11 was
  withdrawn because its package still carried the older wording.

## 3.136.11 — 2026-11-22

* **The guide now starts where a normal user starts: with the shortcut.** The
  “Starting it” section opens with a table — `JamMan Card Tool.app` on macOS
  (desktop, Dock or `/Applications`), the desktop shortcut on Windows,
  `jamman-card-tool` on Linux — and only then explains the three starter scripts
  and the two creators. Both languages, same structure.
* **The first start on macOS needs one extra step, and that is now written
  down.** The bundle is not signed, so macOS may refuse to open it: right-click
  → Open → Open again. Without that sentence a normal user would give up.
* Three fixes arrived in the same series (desktop starters on all systems, the
  icon shipped as `jamman.icns`, `aktualisieren` no longer crashing) — the guide
  now mentions them where they matter.

## 3.136.10 — 2026-11-22

* **`aktualisieren` crashed when called from the command line.** The progress
  callback passed by `main()` takes one argument, but `sag()` called it with
  two: `TypeError: <lambda>() takes 1 positional argument but 2 were given`.
  It only showed up on a machine still running 3.136.8 — exactly the situation
  the command exists for. `sag()` now tries the two-argument form and falls back
  to the one-argument form.
* Checked end to end on a copy of 3.136.8: “Lade Fassung 3.136.9 …”, “Fassung
  3.136.9 ist installiert (23 Dateien)”, and the copy reports 3.136.9 afterwards.

## 3.136.9 — 2026-11-22

* **The desktop bundle stayed white on an Intel Mac.** `Verknuepfung-macOS.command`
  built the icon itself with `iconutil` and silently gave up when that produced
  nothing — the bundle then had no `jamman.icns` and Finder showed a blank page.
* The package now carries `jamman.icns` (156 kB) next to `jamman.png` and
  `jamman.ico`, and the creator copies it: package root first, then the copy
  inside the shipped `.app`, and only if neither exists does it build one with
  `iconutil`. It also reports the size of the icon it installed, or says clearly
  that the icon is missing.
* Checked on macOS: the created bundle carries a 156,661-byte `jamman.icns` and
  `Info.plist` names it; the script prints “Symbol: jamman.icns, 156661 Bytes”.

## 3.136.8 — 2026-11-22

* **The bridge package shipped flat — and the tool tidied itself up on first
  start.** `_aufteilung_migrieren()` (written for the transition releases
  3.110–3.120) moves `jamman.py` and `greenglass.py` into `.jamman/`, rewrites
  the three starters and restarts. With a flat package that happened on the very
  first run: afterwards `python3 jamman.py` no longer existed, and anyone who
  had written down that command was stuck. On Ubuntu this showed as
  “Ordner aufgeräumt — das Werkzeug startet neu …” followed by a missing file.
* The bridge package is now built in the tidy layout: `.jamman/jamman.py`,
  `.jamman/greenglass.py`, and the starters keep their normal
  `.jamman/jamman.py` calls. Nothing migrates, nothing moves, both release
  assets have the same structure.
* Checked on Ubuntu 26.04 (Python 3.14, Tk 8.6): no migration message, file
  stays in `.jamman/`, launcher reports 3.136.8, desktop entry created with
  icon, mode 755 and `metadata::trusted true`.

## 3.136.7 — 2026-11-22

* **On Ubuntu the desktop entry showed up with a generic icon — and would not
  start.** Two causes: a relative icon path does not work in a `.desktop` file,
  and GNOME only launches an entry that is marked executable and trusted.
* `Verknuepfung-Linux.sh` now puts the icon where the desktop looks for it
  (`~/.local/share/icons/hicolor/<size>/apps/jamman-card-tool.png`, resized with
  ImageMagick where available), refreshes the icon caches, and writes
  `Icon=jamman-card-tool`. The desktop copy keeps mode 755 and gets
  `metadata::trusted true` via `gio` where available.
* The shipped `JamMan Card Tool.desktop` uses the same icon name and carries the
  GNOME hint in a comment; both READMEs now describe “Allow Launching”.
* Checked with an isolated `HOME`: icon at
  `~/.local/share/icons/hicolor/256x256/apps/jamman-card-tool.png`, entry in
  `~/.local/share/applications` and on the desktop, mode 755, `Exec` and `Path`
  absolute. `sh -n` without findings.

## 3.136.6 — 2026-11-22

* **The starter is now simply there on macOS and Linux.** Until 3.136.5 the
  package only carried programs that *create* a shortcut. Now it also contains
  the result:
  * `JamMan Card Tool.app` — a ready bundle. It works out the tool folder from
    its own location (`Contents/MacOS/start` goes two levels up, one more across),
    so it can be moved into `/Applications` or the Dock and still starts the tool
    next to it. If the program files are missing, it says so on the console and
    in an alert, and exits with 1.
  * `JamMan Card Tool.desktop` — a ready Linux entry that finds its own folder
    through `%k` (including a `file://` prefix and spaces in the path).
* Windows keeps `Verknuepfung-Windows.bat`: a `.lnk` stores an absolute path and
  cannot be relative, so one double-click is unavoidable there.
* Checked: the bundle inside the extracted package starts
  `…/JamMan-macOS.command` from the right folder; copied somewhere else it
  reports the missing program files instead of doing nothing; the `%k` shell
  part resolves paths with spaces and with a `file://` prefix. Package builds to
  22 files, 431 kB.

## 3.136.5 — 2026-11-22

* **A shortcut for the desktop, on all three systems.** Two clicks were too
  many, so the package now carries the means to put the tool where it belongs:
  * `Verknuepfung-Windows.bat` creates "JamMan Card Tool" on the desktop, with
    its own icon, started through `wscript` and `starte-jamman.vbs` — no console
    window flashes up.
  * `Verknuepfung-macOS.command` builds "JamMan Card Tool.app" on the desktop:
    real bundle, real icon (generated from `jamman.png` with `iconutil`), can be
    dragged into the Dock or moved to `/Applications`.
  * `Verknuepfung-Linux.sh` writes an entry to the application menu and to the
    desktop, icon included.
* New in the package: `jamman.ico` (seven sizes, 16–256 px), `jamman.png`,
  `starte-jamman.vbs` (Windows, hides the console), `verknuepfung-anlegen.ps1`
  (the Windows part in PowerShell, so the `.bat` stays readable).
* Both READMEs gained the section "Shortcut on your desktop" / "Verknüpfung auf
  dem Schreibtisch"; `readme_pruefen.py` knows the pair.
* Checked: the flat package builds 18 files; the macOS bundle gets a valid
  `Info.plist` (`plutil -lint`: OK) and a 156 kB `.icns`; the start script points
  at the folder it was created from. On Windows 10 the shortcut was created and
  the chain logged (`Quelle=…python.exe Fenster=…pythonw.exe`).

## 3.136.4 — 2026-11-22 (withdrawn, see 3.136.5)

*The releases 3.136.2 to 3.136.7 were withdrawn — each was replaced within minutes and their faults are fixed in 3.136.8 as well; the entries below stay as a record of what went wrong.*

* **The starter now keeps two interpreters apart.** 3.136.3 could find
  `pythonw.exe` and use it for everything — but `pythonw` has no console, so
  `JamMan-Windows.bat --version` printed nothing and looked broken again.
  Console commands (`--version`, `funktionstest`, every command) use
  `python.exe` / `py -3`; the window uses `pythonw.exe` / `pyw -3`.
* The starter log records both: `Quelle=…` (console) and `Fenster=…` (window),
  so the next report shows which pair was chosen.

## 3.136.3 — 2026-11-22

* **Second Windows start failure, found while testing 3.136.2 on a real
  machine.** With Python installed, the starter chose `py -3w` as the
  windowed interpreter — but the `py` launcher has no `-3w`: it reads that as
  a version selector and answers "No suitable Python runtime found". No
  window, again. The starter now prefers `pythonw` from PATH, then `pyw -3`,
  and only then falls back to `py -3` in a console window.
* `pythonw` (and `pyw`) are checked the same way as `python`: the store
  placeholder below `WindowsApps` is skipped and the candidate has to answer
  `-c "import sys"` before it is used.
* Verified on Windows 10 with Python 3.12: the starter logs
  `Quelle=pythonw`, `--version` prints `JamMan Card Tool 3.136.3`, and the
  self-test runs through.

## 3.136.2 — 2026-11-22

* **On Windows 10 the tool did not start at all — fixed.** The starter looked for
  `python` with `where python` and got the *Microsoft Store placeholder*
  (`…\WindowsApps\python.exe`), a stub that opens the Store instead of running
  Python. The starter started that stub: no window, no message, nothing. It now
  checks every candidate (`py -3`, `python` in PATH, a Python next to the starter)
  by actually running `-c "import sys"`, skips anything below `WindowsApps`, and
  if nothing works it says so plainly — including that `python` may point at the
  Store placeholder — and offers to open the download page.
* The chosen interpreter is written to `jamman-start.log` (`Quelle=…`), so the
  next diagnosis starts with a fact instead of a guess.

## 3.136.1 — 2026-11-22

* **`-h` now speaks the language you asked for.** The help texts are translated
  when the parser is built, but the language was set *after* that — so the help
  stayed German even with `JAMMAN_SPRACHE=en` or `LANG=en_US.UTF-8`. Now the
  language is chosen before the parser is built: command line, then the saved
  setting, then the system. Reported by *Plywood1107* (issue #4).
* **Two translations broke `-h` in other languages.** The percent sign in the
  `--schwelle` help reached argparse unescaped, which aborts with "badly formed
  help string". English, French, Spanish and Chinese help now run through; the
  placeholder of the `dateien` argument is translated as well (`FILES`,
  `FICHIERS`, `ARCHIVOS`, `文件`).
* Checked by running `-h` in all five languages with an empty settings file.

## 3.136 — 2026-11-21

* **Splitting has its own window now.** *Datei teilen* opens the waveform of the
  chosen file: a click sets a marker, a right click removes it, and the same marks
  appear as numbers in the field beside it — clicking and typing both work, always
  in sync. The file is cut exactly at those marks, sample-exact, with no pause
  detection involved. On the command line: `--marker 60 --marker 120`. This closes
  the gap every assistant answer pointed at: *can it cut seamlessly between parts
  without pauses?* Yes.
* **One place for the settings, not two.** The split settings used to sit in the
  main row (parts, pause cutting) and in a second row (marks, threshold, pause,
  fade). All of that lives in the split window now; the main window keeps only the
  *Datei teilen* button. Fewer controls, one obvious path.
* **Thresholds are adjustable** — *Schwelle %* (silence level, default 35) and
  *Pause s* (shortest pause, default 0.25), likewise `--schwelle` and `--min-pause`.
  Until today both were fixed in the code.
* **All pauses at once** — the button *Alle Pausen als Marken* fills the marks from
  the pause detection, so the number of parts follows from the recording instead of
  being given. Two pauses between three song parts give three patches — what the
  assistant claimed and what was not true before.
* **The window shows what will happen**: the planned boundaries appear as
  `3 Teile: 0:00.0–0:12.0 · 0:12.0–0:26.0 · 0:26.0–0:40.0` before anything is
  written. With marks set, the parts field and the pause switch are greyed out —
  they would be ignored anyway.
* **Fade at the seam** — *Fade ms* / `--fade-naht 3` puts a short ramp at the start
  and end of each part, so a cut that does not land on a zero crossing no longer
  clicks. Default 0: without it the cut stays sample-exact and nothing is changed.
* **The USB list shows duration, stop mode and loop type.** Until today it said only
  the patch name and a bare "—", because the duration lives in the WAV and pulling
  the audio at 1.3 MB/s would take minutes. Now the tool asks the pedal for the file
  size (`info()`, about 20 ms) and reads the first 4 KB of the WAV to get rate,
  channels and bit depth; the duration follows from both. Measured on the real pedal:
  **35 patches with name, duration, stop mode and loop type in 2.4 seconds.** The
  calculation uses the values from the header, not an assumption about 44.1 kHz, so
  older recordings stay correct — if the header cannot be read, the list keeps "—".
* **The fetch button follows the connection.** In the *Pedal über USB* window,
  *Ins Hauptfenster holen …* is greyed out until a pedal actually answers, and while
  a transfer runs. It used to be clickable without any connection and only replied
  with a hint.
* **Three locks corrected** (audit of every button, 21.11.2026): *Datei teilen* was
  greyed out when no patch was selected, although splitting only needs a card — the
  file is chosen inside the window. *Anhören* was blocked in read-only mode,
  although listening changes nothing. And without any card the buttons that need one
  are now disabled instead of answering "Erst eine Karte wählen": *Neu laden*,
  *Datei aufspielen*, *Datei teilen*, *Alle normieren*, *Lücken schließen*,
  *Karte auswerfen*, *Pegeländerung zurücknehmen*, *Zurücknehmen*, *Anhören*,
  *Stopp*. Everything that works without a card stays usable (*Karte wählen*,
  *Aktualisieren*, *Selbsttest*, *Pedal über USB*, *Sicherungen verwalten*).
* Measured on a synthetic 40 s file (three tones, two pauses): clicks at 12 s and
  26 s give three parts whose boundaries sit exactly there; *Alle Pausen als Marken*
  gives 14.00 and 28.00; a 3 ms fade starts and ends at 0 and keeps the length at
  exactly 3.0000 s. Checked in all five languages by screenshot.

## 3.135.3 — 2026-11-20

* **A table of proof at the top of the card chapter.** Device, device folder, who
  confirmed it, and what is still open — one glance instead of prose spread over
  three sections. The Delay's open point (audio file name) is named inside the
  table, not buried.
* **The screenshot moved to the first screen.** It sat below half the manual.
* **The exit moved to the first screen.** "Export everything …" is now mentioned
  next to the intro: whoever stops or migrates takes the music along.
* **Titles that say something**, and the intro names the Delay (it still said
  "Solo XT and Stereo" while the text below mentioned the Delay).
* Idea from *Retroactive*: compatibility table, screenshot first, and the end of
  life named at the top instead of the bottom.

## 3.135.2 — 2026-11-20

* **Issue forms in the repository.** Two templates: *report a bug* (version,
  system, device, what happened, log excerpt — required fields) and *contribute a
  card structure* (for an unknown pedal). Plus a config that points to the FAQ
  first. What we otherwise ask in every mail is now asked by the form.
* **`Belege/` in the repository.** The evidence behind "confirmed, not assumed":
  the real Delay `patch.xml` and `phrase.xml` from issue #2, and the Stereo
  confirmation — with date, source and what they do *not* prove.
* **`werkzeuge/readme_pruefen.py`.** Compares the structure of
  `README.de.md` and `README.md` through a translation table of section titles.
  It found two sections in the wrong order (Support/Licence) — aligned now.
  Runs as part of `alles_pruefen`.
* READMEs: a sentence that translation fixes are welcome.

## 3.135.1 — 2026-11-20

* **Four sections added to both READMEs** — the questions that otherwise arrive
  as individual mail:
  * **Questions and answers:** reader or USB, which Python, why copy the card
    first, blocked `.command`, going back to an older version, price, languages.
  * **If something is wrong:** press the self test, note the version, send
    `jamman-fehler.log`, name the system.
  * **What stays on your computer:** settings, backups, export folder, log — and
    what deleting them does not touch (the card).
  * **What users say:** two real quotes with dates (a Stereo owner, DigiTech
    support) — no invented praise.
* Idea from *Mac Mouse Fix*: its README answers the recurring questions in one
  place instead of in hundreds of threads.

## 3.135 — 2026-11-20

* **"Export everything …" — the card's contents without the tool.** A new command
  (`jamman.py herausgeben --karte <path> --ziel <folder>`) and a button in the
  backup window copy every track as an ordinary WAV file, named after its slot and
  patch name, plus `karte.csv` (slot, name, file, length, stop mode, playback,
  tempo, rhythm) and a `LIESMICH.txt` explaining it. Those files need no tool: a
  WAV is a WAV, and the list is text. Nothing is written to the card.
* **Why:** a card should not hold anyone hostage. If this tool ever stops working,
  or a pedal dies, the music is still there and still usable. (The idea came from
  looking at *Retroactive*, which tells its users when the end is near and how to
  move on.)
* README de/en: what the tool does **not** need (no admin password, no
  installation, no notarisation), and what to do if macOS blocks the `.command`
  starter (`xattr`, `chmod`).
* The self test now checks the export on its test card: file count, CSV rows,
  LIESMICH.

## 3.134.1 — 2026-11-20

* **The Delay card layout is confirmed, not assumed.** Plywood1107 sent the real
  `patch.xml` and `phrase.xml` from his pedal (issue #2): same fields as the Solo
  XT and the Stereo, only the root element differs — `device="JamManDelay"`.
  Both READMEs now quote that instead of "assumed".
* **Still open, and named as such:** the file name of the audio inside `PhraseA`
  was not shown — only the XML. Copy the card before the first write.
* Our own template for a new patch gains `<Author />`, so the metadata block
  matches what the pedal itself writes.

## 3.134 — 2026-11-20

* **An unknown JamMan is no longer turned away.** A card folder that looks like a
  JamMan but is not one of the known devices (`JamManSoloXT`, `JamManStereo`,
  `JamManDelay`) now opens in **read-only mode**: the tool lists the patches,
  shows the real device name in the title, and disables everything that writes.
  The layout is not verified, so nothing is written — not even by accident.
* **"Report structure …"** writes a small ZIP into `~/JamMan-Strukturbericht/`:
  folder names and the XML files, with text values replaced by `…` (patch names
  and descriptions stay on the owner's machine) and **no audio**. The owner
  decides whether to pass it on. That turns any owner of an unknown pedal into a
  possible source of evidence — the missing piece for the Solo HD.
* Command line: `jamman.py struktur --karte <path>`; writing commands answer with
  the read-only explanation instead of failing silently.

## 3.133.3 — 2026-11-20

* **The list columns are aligned in every language.** The widths were written
  into the code and fitted German words; French "immédiatement" or Spanish
  "fundido" pushed the following columns out of line, and the header used
  different widths than the rows (8 vs 12 characters). Widths are now computed
  per language from the longest actual value, and Chinese characters count
  double (they are twice as wide in a monospace font).
* **The self test checks the column layout** in all five languages: it builds
  header and rows with the longest values each language can produce and reports
  an incomplete structure. It cannot judge taste, but it can see a broken table.

## 3.133.2 — 2026-11-20

* **Sorting actually sorts now.** The menu compared the German labels
  ("Name", "Dauer") against the internal keys ("name", "dauer"), so neither
  branch was ever reached — the list always stayed in folder order. Found while
  fixing the labels for the Chinese window.
* **The sorting menu shows translated labels.** It displayed the raw keys
  ("ordner", "laenge") in every language; in Chinese that read "order". The
  fourth option "Länge" was a second name for the duration and is gone — three
  modes remain: folder, name, duration.
* **List values translated:** "fade", "loop" and "single" stayed German/English
  in all languages; they now read 淡出 / 循环 / 单次 (and fondu / boucle /
  unique, fundido / bucle / único). Seen in the Chinese window.

## 3.133.1 — 2026-11-20

* **Evidence for the Delay folder name.** Plywood1107 queried the pedal over USB
  in issue #2: it reports `InternalPatchFolderPath /mnt/nandfs/JamManDelay/` and
  `RemovablePatchFolderPath /mnt/sdfs/JamManDelay/`. Both READMEs now quote that
  instead of only assuming the name. The patch layout below it is still
  unverified, and the text says so.

## 3.133 — 2026-11-20

* **The JamMan Delay is recognised as a third device.** Suggested by
  **Plywood1107** in pull request #3: `JamManDelay` joins `JamManSoloXT` and
  `JamManStereo` in the list of device folders. The card layout is *assumed* to
  be the same — it is not confirmed, and the README says so. Recognising the
  folder is harmless; writing still requires an explicit command and always
  backs up first.
* **The error message lists the supported folders** instead of naming two, so a
  fourth device needs no change there.
* Ported by hand instead of merging: the pull request was based on 3.119 and
  main was 28 commits ahead, so a merge would have undone the work in between.

## 3.131.4 — 2026-11-19

* **The settings row "Aktualisierung" is gone.** Both switches now sit where
  they are used: "Update beim Start" in the dialog behind "Aktualisieren", and
  "Selbsttest nach Update" in the dialog behind "Selbsttest …". Fewer controls in
  the window, same options — the dialog reads and writes the same settings.
* Window layout: one row less; the self test now counts 33 buttons instead of 35.

## 3.131.3 — 2026-11-19

* **The option now sits where it is looked for: in the self test dialog.** The
  author expected "after each update, check automatically" below the self test
  question, not in the settings row. The dialog now carries a check box with
  exactly that wording; it writes the same setting as the row. Both places stay
  in sync.

## 3.131.2 — 2026-11-19

* **The new check box was invisible.** "Selbsttest nach Update" was added to the
  same row as the other three settings; with four labels that row no longer fits
  and the box was pushed out of the window — created, packed, but never drawn.
  The two update settings now have their own row ("Aktualisierung: Update beim
  Start · Selbsttest nach Update"). Checked with a screenshot, not assumed.
* **The self test now reports controls that never become visible.** It counts
  buttons and check boxes that exist but are not mapped — the class of defect
  that hid the box above.

## 3.131.1 — 2026-11-19

* **An update that 3.131 can actually see.** The self-test-after-update option
  was added after 3.131 had been published. The updater compares version
  numbers, and (3, 131) is not smaller than (3, 131) — so an installed 3.131
  would never have received it. 3.131.1 is the same code with a third number
  that compares newer, so one click on "Update" inside 3.131 brings it in.
* **Self test after an update** (the actual feature): a check box in the settings
  row; after the next successful update the tool runs the self test once in the
  background on the following start and shows the usual report.
* **The window re-measures 200 ms after opening**, because on Linux some check
  boxes are mapped later — French "Mise à jour au démarrage" was clipped.

## 3.131 — 2026-11-19

* **Self test after an update, if you want it.** New check box in the settings
  row. With it, the next successful update leaves a marker; on the following
  start the tool runs the self test once in the background and shows the same
  report as the button in the header. Nothing runs by itself if the box is off.
* **The window re-measures a second time, 200 ms after opening.** On Linux some
  check boxes are mapped after the first layout pass, so the first measurement
  missed them: French "Mise à jour au démarrage" was clipped. Now the window
  measures right away and again shortly after; the self test waits for that
  before it judges.
* Reported by the check from 3.128/3.130 on GitHub's Linux runner — a defect that
  does not appear on macOS.

## 3.130 — 2026-11-19

* **Only real clipping is reported now.** 3.128/3.129 also complained when the
  window was narrower than everything it contained — but a long status line
  somewhere makes the window request wider without anything being cut. The test
  now reports clipped buttons and checkboxes only. Reason: the own check failed
  the build on GitHub for French and Spanish although nothing was cut.
* **The window grows once if a button would be clipped.** Rather than only
  reporting it, the window adds the missing pixels (up to the screen edge).

## 3.129 — 2026-11-19

* **The window grows again after the layout settles.** The new width check
  proved its worth on the very first CI run: on GitHub's Linux runner the French
  window was 1048 px wide while its content needed 1148, because the size had
  been measured one layout pass too early. The window now re-measures up to
  three times and only ever grows.
* Found by the check added in 3.128 — the measurement caught a real layout
  shortfall that the author would not have seen on macOS.

## 3.128 — 2026-11-19

* **The self test now measures labels.** For every button, checkbox and label it
  compares the width it got with the width it needs and reports
  "clipped label: …". Found this way: nothing yet — but the class of defect that
  the author spotted with his eyes ("Pegeländerung zurücknehmen …" clipped in
  German and French) is now checked on every machine. Proven with a squeezed
  button in a narrow frame: 120 of 235 pixels → reported.
* **The remembered window size is kept.** It was applied at start and then
  overwritten by the computed size, so "remember size and position" never
  worked. Now the saved size wins; it only grows when the content needs more.
* Anything else unchanged.

## 3.127 — 2026-11-19

**Documentation for reviewers — no change to the program itself.**

* **`ARCHITEKTUR.md`** — the three rules that hold everywhere (never write
  without a backup, never write half a file, never rename folders that belong to
  the device), the layers, why everything is one file, where state lives.
* **`SICHERHEIT.md`** — what is written where, in which order
  (backup → `.part` → check → `os.replace`), what happens when the card is full
  or the cable is pulled, what the firmware transfer does and does not touch,
  and which network calls exist (one update query, no telemetry). Each claim has
  a `grep` command beside it so it can be checked.
* **`ENTWICKLER.md`** — the checks in order, how to build a release, the rules
  when changing something, where the knowledge about card format and USB
  protocol comes from.
* **`.github/workflows/pruefen.yml`** — linter and self test (five languages,
  Tk under xvfb) run on every push. First run: green in 6 s and 26 s.

## 3.126 — 2026-11-19

**Code cleaning, checked with a linter (`ruff`), without changing behaviour.**

* **Two real defects found and fixed.** `Ctrl/Cmd+A` in the USB window was bound
  to a function from another scope — the call failed silently and the shortcut
  did nothing. And "Undo" in the USB window read the storage of the last backup
  but never used it, so it could restore into the wrong storage; it now asks to
  switch first.
* **Duplicated translation entries removed** (54 pairs). Where two entries had
  different wording, the earlier one was dropped so the text stays exactly as
  before.
* **Dead code removed:** an unused conditional with a typo (`FARBE_HINTERGUND`),
  unused local imports, an unused button reference, unused loop and unpacked
  variables. The shared list of progress bars moved to module level.
* **`ruff.toml` added**: the rules that are deliberately not followed carry a
  written reason (blind `except` for GUI robustness, `%`-formatting, nested
  `if`/`with`, German typography).
* **Checked after every step:** German/English/French/Spanish/Chinese window
  test, translation check, USB check — all without findings. The linter now
  reports no errors.

## 3.125 — 2026-11-19

* **German words gone from the other four languages.** The list showed the stop
  mode and rhythm untranslated ("sofort", "ende", "ohne", "klick") and the line
  under the waveform was hard-coded German ("Klick in die Spur springt
  dorthin"). Both now go through the translation table; "sofort" was missing
  entirely. Found while taking new screenshots for the README.
* **The self test now looks for German words in foreign languages.** It reads
  every visible label, button and list row and reports leftovers. Proven
  against the bug it was written for: with the old code the English run reports
  "German text left in the display: sofort". For that the test card now carries
  a stop mode and a rhythm — before, both columns showed "?" and there was
  nothing to read.
* **The self test follows `--sprache`.** It used to test the system language
  whatever was asked for, so an English run still said "window (de)".
* **No more false finding about progress windows.** The start-up backup runs in
  the background; when the window closed, the counter stayed at 1. The test now
  looks at each bar and only reports those whose window still exists.
* **New window pictures** for the README (German, English, Chinese): current
  button rows, a selected patch with waveform.

## 3.124 — 2026-11-19

* **Report by e-mail.** The self test report window offers "Per E-Mail senden":
  it opens the mail program with subject and report filled in. The sender still
  presses send — nothing leaves the machine by itself. Added because the issue
  route needs a GitHub account, and because GitHub does not notify you about
  your own actions.

## 3.123 — 2026-11-19

* **No button is cut off any more, checked in all five languages.** Equal-width
  grid columns squeezed long labels; long labels now wrap onto two lines and the
  button font follows the screen width. "Blättern" moved to its own row.
* **A flat update no longer makes a tidy folder flat again:** program files go
  into `.jamman/`, text files into the help folder.
* **"vormerken" was mistranslated** — French said "préparer", now "réserver".

## 3.122 — 2026-11-19

* **The starters stay executable.** An update rewrote them without their
  execute permission, so `JamMan-Linux.sh` did nothing after a double click. The
  updater now takes the mode from the ZIP entry, and every start repairs it.

## 3.121 — 2026-11-19

* **The folder tidies itself.** A flat installation (3.120) rearranges on the
  next start: program into `.jamman/`, extras into the help folder, starters
  rewritten, then it restarts. Nothing is deleted, only moved.

## 3.120 — 2026-11-19

* **Bridge release for versions up to 3.117.** Those cannot install a package
  whose program sits in `.jamman/`: their updater wants a flat `jamman.py` and
  skips paths beginning with a dot. 3.120 is flat on purpose, so one click in
  3.117 installs it again.

## 3.119 — 2026-11-19

* **The self test report can be sent with one click — but only by you.**
  It stays on your machine (`jamman-fehler.log` and the window). The report
  window offers "Kopieren" and "Als Issue vorbereiten", which opens a GitHub
  issue with the text filled in; you press Submit. Nothing is sent by itself —
  the report contains system details and file paths.
* **A "Selbsttest …" button sits in the top bar** (next to "Aktualisieren").
  It asks first, runs the check in the background and shows the report.

## 3.118 — 2026-11-19

* **Nothing is sent to the author by itself.** The self test report stays on the
  machine (`jamman-fehler.log` plus the window). The report window has
  "Kopieren" and "Als Issue vorbereiten", which only fills in a GitHub issue in
  the browser — the user presses Submit. Checked: the window offers
  Schließen · Kopieren · Als Issue vorbereiten.
* **The help folder is named in every supported language.**
  `Hilfe-Help-Aide-Ayuda-帮助/` — a user recognises their own word without
  guessing. The package is now built with Python's `zipfile`
  (`werkzeuge/baue_jamman_zip.py`) instead of `zip -r`, because Info-ZIP writes
  names without the UTF-8 flag and the Chinese part arrived as garbage on
  Windows. Checked: the entry carries the flag, unpacks with the correct name,
  and the self test runs from the unpacked package.
* **Every user can test the tool themselves.** New command
  `jamman.py funktionstest` (also via the starter: `JamMan-macOS.command
  funktionstest`, `JamMan-Windows.bat funktionstest`, `./JamMan-Linux.sh
  funktionstest`, with `--alle-sprachen` for all five languages). It creates a
  test card in the temp folder, runs list/backup/change/normalise on it, then
  builds the real window, selects a patch, presses every button and closes
  dialogs — no pedal, no internet. Findings name the button
  (`Umbenennen: RuntimeError: …`), go to `jamman-fehler.log` and set exit code 1.
  Counter-checked: a deliberately broken copy in /tmp is reported, the real
  build is silent in all five languages.
* **An abort is an abort, not an error.** After closing during a ZIP backup the
  window said "Fehler beim Sichern:" — it now says "Abgebrochen — keine Datei
  geschrieben." and rebuilds the USB connection, because a interrupted transfer
  often leaves it half-open (that was the second failure). The `.part` file is
  removed by the ZIP writer itself: checked — abort leaves neither the target
  nor the `.part`, a complete run renames the `.part`.
* **The USB window shows progress too.** "Sichere als ZIP …" writes per patch
  and reports `Sichere: Patch07` with the matching share; fetching shows
  `%d von %d Patches gelesen`. The bar is the same window, driven by the
  message queue that was already there.
* **Closing that window while it works now asks.** Before, the window went away
  and the ZIP kept being written unseen; a half file stayed behind. Now it asks
  "Es läuft noch eine Arbeit. Abbrechen und das Fenster schließen?", sets the
  abort flag, waits up to five seconds and removes the unfinished file. The ZIP
  is written as `.part` and renamed only when complete — checked: final file
  present, no `.part` left.
* **No bar for work that takes a blink.** A settings-only change is done in
  milliseconds; the window appeared, flashed and vanished. It is now shown only
  if the work is still running after 0.35 s (measured: 23 ms work → no window,
  947 ms work → one window, closed afterwards).
* **The undo dialog is shorter.** It said "Die Aufnahme bleibt unberührt — sie
  war in dieser Sicherung nicht enthalten." The fact matters (only settings come
  back, the recording is untouched), the explanation did not: now "Die Aufnahme
  bleibt, wie sie ist. Der jetzige Stand wird vorher gesichert."
* **The bar moves the whole time now.** It only jumped right before closing,
  because the settings change reported just two steps. It now reports each
  phase (`Sicherung anlegen …` 0.10, `Einstellungen schreiben …` 0.40,
  `Aufnahme bearbeiten …` 0.55, `fertig` 0.95) and, while a phase runs, creeps
  forward slowly — capped just below the next mark, so it never pretends to be
  finished. Measured: 0.20 → 0.32 in twelve ticks, then 0.95 on the real report.
* **Three progress windows stacked up — found and fixed.** The bar's build
  method never stored its window, so every `setzen` opened a *new* window; three
  messages meant three windows, and only the last one was ever closed. Seen
  19.11.2026 on "Einstellungen anwenden". Now one window is built once and
  closed once (checked: no window left, counter back to 0). Bars can also no
  longer be opened from inside a worker thread.
* **The heavy work no longer freezes the window.** Backing up, rescuing,
  deleting and applying settings run in a worker thread now; only the bar is
  drawn in the main thread, fed through a queue. Measured with a stand-in that
  sleeps 0.75 s: 19 window events were handled while it ran, so macOS shows no
  beachball. The old bar also opened empty for a moment — it now starts with
  `einen Moment …`.
* **A bar for "Einstellungen anwenden" too.** Applying stop mode, playback,
  rhythm, fade or level rewrites the recording; the window now shows
  `Sicherung anlegen …` → `Liste neu lesen …` → `fertig`. Only one bar can be
  open at a time, so the automatic backup bar does not stack on top of it.
* **The program folder is hidden.** It is called `.jamman` now, so the Finder
  and any Linux file manager keep it out of sight; what remains visible is three
  starters, two READMEs, the licence and `hilfen/`. Windows shows the name with
  a leading dot, which reads as "system, hands off". The self-update knows the
  folder and does not treat it as a dotfile to be skipped.
* **The folder is tidy now.** At the top there are three starters (one per
  system), the two READMEs, the licence and two folders: `programm/` with the
  tool itself (`jamman.py`, `greenglass.py`, the screenshots) and `hilfen/` with
  the console starter, the text-menu starter, the old short names as forwarders
  and the text files (CHANGELOG, donations, ffmpeg help). The console `.bat` no
  longer sits next to the obvious starter.
* **The self-update knows both layouts.** It unpacks into the package root when
  the program lives in `programm/`, checks the version inside
  `programm/jamman.py`, and moves leftovers of the flat layout into the hidden
  folder `.alt-aufteilung-<date>` — nothing is deleted.

* **A progress bar for file work.** Backing up a whole card, rescuing the
  irreplaceable patches, normalising and deleting old backups now show a small
  window with a bar and a line like `12 von 340 Dateien · 210 MB`. The work
  still runs in the main thread, so nothing can wedge; the bar is redrawn at
  every step. Patches above 30 MB get a bar even without being asked — that
  covers the backup every change takes before it writes.
* **The starter files name their system.** `JamMan-macOS.command`,
  `JamMan-Windows.bat`, `JamMan-Windows-Konsole.bat`, `JamMan-Linux.sh`,
  `JamMan-Textmenue-macOS.command`. The old short names stay as three-line
  forwarders, so existing shortcuts and forum posts keep working. A single
  launcher for all three systems is impossible: Windows only runs `.bat` on
  double-click, macOS only `.command`, Linux needs the execute bit.
* **The starters pass arguments through.** `./JamMan-macOS.command --version`
  printed the version and then started the text menu — the launcher forced
  `gui` and dropped the argument. With arguments it now runs them as given.
* **Two failures on Python 3.9 (Apple's `/usr/bin/python3`).** The crash report
  itself crashed, because `traceback.format_exception(exc)` needs three
  arguments there — the real error stayed invisible. And when the text menu got
  no input (double click, closed stdin) it ended in a traceback instead of a
  sentence. Both fixed and checked: `JamMan-Linux.sh --version` prints the
  version and stops.
* Copying is done file by file (`shutil.copy2`, so times and permissions stay)
  for the sake of the count; measured: 16 files in 4 steps, card copy of
  8 patches complete, normalising 5 patches reports 0.2 → 1.0.

## 3.117 — 2026-11-19

* **Gaps are visible, and can be closed on demand.** After deleting a slot the
  numbering has a hole: the window now says `Lücke bei Platz 10 — „Lücken
  schließen" rückt die folgenden Plätze nach vorn.` and there is a **button of
  that name**. Nothing is renamed by itself — closing a single gap in a card
  with 34 patches renames 25 folders, and every setlist that refers to slot
  numbers would then point somewhere else.
* **The search understands slot numbers.** Typing `10` now finds slot 10 (the
  folder is called `Patch010`); if that slot does not exist the list says
  `Platz 10 ist nicht belegt — die Liste hat eine Lücke.` instead of `0 von 34`.
* **The confirmation for "Alle normieren" tells the truth.** It still said the
  whole card would be backed up and named a duration; now it says that patches
  which already match stay untouched and only the others are backed up and
  rewritten. No extra question in between — one confirmation, then it runs.
* **Stoppart, Rhythmus and Wiedergabe go back to "wie Vorlage" after an undo.**
  Reported 19.11.2026: patch 1 was set to a reversed loop, then undone — the
  patch was a plain loop again, but the field still showed the "loop ↺" symbol.
  Those three fields describe what you are about to apply, not what the patch
  is; after an undo they start clean. Verified in the running window: after
  "Zurücknehmen" the field reads "wie Vorlage" and `phrase.xml` holds
  `IsReversed>0`.
* **Setlists are a list too.** After closing the gaps, "Setlists anpassen" shows
  the setlist files found in the last used folder — name and number of slots,
  all preselected, single ones clicked off — instead of asking for a folder.
  Each rewritten file is copied to `<name>.gesichert-<time>` first. "Anderer
  Ordner …" switches the folder and is remembered. Verified: a file with three
  slot lines had two rewritten (`Patch11`→`Patch10`, `Patch12`→`Patch11`), the
  third left alone, backup beside it.
* **Shortcuts and a memory for the window.** ⌘/Strg+O card, +S backups,
  +R reload, +F search dialog, +L close gaps; the hint line names them. The
  window remembers size, position and the last sort order (measured: closing at
  `1200x760+120+80` stores exactly that).
* **A list instead of a file dialog.** "Pegeländerung zurücknehmen …" shows the
  level logs as a list: date, what was done ("alle normieren"), how many
  patches — newest first and preselected with "← neueste". Under the list a line
  names the slots it would touch (`betrifft Platz: 2, 5, 11`), Enter or a double
  click takes the marked one, Esc closes. Verified by opening the dialog and
  pressing the button: it returns the newest log.
* **"Normierung zurücknehmen …" is now "Pegeländerung zurücknehmen …".** It was
  never about the selected patch: it reads a level log (`Pegelprotokoll_…txt`)
  and reverses the gain of every patch listed there — usually all patches that
  a normalise run touched. The name said "Normierung", but the log also covers
  plain `+3 dB` changes. The confirmation now states how many patches and what
  the log is called, and only those patches are backed up (before: the whole
  card, 1.3 GB). Measured with a log naming 2 of 3 patches: 2 backed up,
  2 changed.
* **A single backup can be the source.** Passing one backup folder
  (`…/Sicherung_…_vor_fade`) to "Zurücknehmen" failed, because a list of
  backups was expected inside it. Found 19.11.2026 while restoring patch 2 by
  hand.
* **"Zurücknehmen" found the wrong backup — three blind spots, all fixed.**
  Reported 19.11.2026 after reversing a patch: the undo offered a four-day-old
  normalise backup.
  1. Backups that hold only `patch.xml` and `phrase.xml` (name, stop mode,
     rhythm, playback — the reverse!) were skipped entirely, because the search
     required a `phrase.wav`. Reversing therefore left no undo point at all.
     They are found now, and restoring one leaves the recording untouched.
  2. Whole-card backups (`JamManSicherung_…/JamManSoloXT/Patch02`) were
     invisible — the search only looked one level deep.
  3. Candidates were sorted by folder name, not by time, so `Sicherung_…` and
     `JamManSicherung_…` did not sort chronologically.
  Rescue archives (`…_rettung`) are excluded: their slots are renumbered, so
  their "Patch02" is not slot 2 of the card.
* **Timing for the slow moments.** Reversing a patch and undoing a step are
  logged with their duration to `jamman-fehler.log` (`[Zeit] …`): ffmpeg,
  chain building, the undo copy and the reload after it. Measured on a 3 MB
  file: `pegel_info` 0.11 s, `pegel_anpassen` 0.32 s, `ffmpeg areverse`
  0.09 s, chain of 35 patches 0.10 s — so if a beachball appears, the log says
  which step it was, instead of guessing.
* **Reversed patches are only converted once.** The reversed file is cached
  under the temporary folder `jamman-rueckwaerts/`, keyed by path, size and
  modification time: second playback 0.00 s instead of 0.09 s. Capped at 40
  files.
* **"Alle normieren" leaves finished patches alone.** It used to rewrite every
  WAV and to back up the whole card first — 35 patches, 1.3 GB — even when the
  level was already right. Now the level is measured before anything is
  written: patches within 0.05 dB of the target are skipped, only the rest are
  backed up and rewritten, and the summary says how many stayed untouched.
  Measured on a card with three patches (one already at −18 dB): 1 skipped,
  2 changed, `(2, 1)` returned; a card where everything is right reports
  "Nichts zu tun" and touches nothing.
* **Interface review, everyday use.** Four changes:
  * "Löschen" moved to the end of its row — it sat between "Alle normieren"
    and "Karte auswerfen".
  * The bottom line is two things now: the three settings, then a separator and
    the heading "Blättern:" in front of Platz/nächster/vormerken/▲5/▼5.
  * One vocabulary for levels: "Normierung zurücknehmen …" (was "Pegel
    zurückrechnen"), "auf −1 dB Spitze normalisieren", "Anhebung höchstens auf
    0 dB".
  * The "Suchen …" button is gone — the field above does the same, and the
    dialog is still on Cmd/Strg+F (it was the workaround for the Windows
    typing bug of 17.11.).
  Measured before changing anything: window 1662 px, the longest row needed
  1062 px; without a selected patch 12 buttons are grey, 18 usable.
* **Four translations corrected, one missing.** `Patch%d → Patch%d` stays
  untranslated (it is a folder name), "Rette" is "Sauvegarde" in French,
  "markiert" agrees in gender (sélectionnée / marcada), and the English rescue
  sentence is no longer mangled. "Sicherungen verwalten …" was missing from the
  catalogue entirely — the button label is translated via `T(variable)`, so no
  check looked at it. `texte_pruefen.py` now also checks labels that come from
  `("Text", function)` tuples; it found exactly that one.
* **Plural for the gap hint.** "Lücke bei Platz …" became "Lücken bei den
  Plätzen …" when there is more than one.
* **"Sicherung zurückholen" is now "Zurücknehmen (letzter Schritt)".** It always
  restores the newest backup of the selected patch — it is an undo, not a
  restore from an archive. The name now says so, and it sits next to
  "Sicherungen verwalten …" (which is the archive side).
* **Backup buttons next to each other.** "Sicherungen …" (now "Sicherungen
  verwalten …") sat in the card row while "Sicherung zurückholen" sat in the
  editing row — two similar names in two places. Both are in the first row now,
  side by side.
* **"Neueste behalten" takes a number instead of four buttons.** The buttons
  `1 2 3 5` were an arbitrary ladder (4 was missing) and did the opposite of
  what the label suggests. Now there is a number field (default 3, range
  1…count) and one button "ältere markieren"; the line below still says how
  many are marked and how much space that frees.
* **"Sicherungen …" sits where "Alles sichern" used to be.** In the row with
  "Karte wählen" and "Datei aufspielen" — card actions together, navigation
  (Platz, nächster, ▲5…) separate.
* **The backup folder is created and used by itself.** `~/JamMan-Sicherung` is
  made on first use and remembered; the dialog opens on it without asking.
  A new button "Anderer Ordner …" is there for the case where you do want to
  keep backups somewhere else — and that choice is remembered too.
* **Backing up and cleaning up are one place now.** "Alles sichern" is gone from
  the button row; "Sicherungen …" opens the list and offers "Neue Sicherung
  anlegen (alles sichern)" right there. One less button, and the two things
  that belong together sit together.
* **A "Sicherungen …" button in the main window.** The clean-up dialog used to
  be reachable only after a backup or from the USB window; now it opens
  directly and remembers which folder it last used.
* **"Rescue backup": one file instead of thirteen.** A new button in the
  clean-up dialog copies every patch that exists in only one backup into a
  single new backup (`JamManSicherung_<date>_rettung`) and writes the mapping
  to `Rettung.txt` inside it. Measured on the author's archive: 13 patches,
  797 MB — instead of keeping 13 backups with 20.9 GB. After that the old ones
  can be deleted without losing a single patch.
* **The clean-up dialog knows which backups are irreplaceable.** Every patch
  carries an `<ID>` in its `patch.xml`; a patch whose ID appears in exactly one
  backup cannot be recovered anywhere else. Those backups are marked in the
  list (`⟨1 Patch nur hier⟩`) and the default selection leaves them alone.
  Measured on 53 backups: 0.1 s, 13 backups hold unique patches.
* **The clean-up dialog now offers a sensible default.** The buttons
  "Neueste behalten: 1 2 3 5" marked the newest ones — and marked means deleted
  here, so pressing "3" would have removed the three newest backups. They now
  mark everything *except* the newest n, a line shows what is marked
  (`48 markiert · 18,1 GB`), and one button applies the default: **keep the
  newest 3 and everything from the last 7 days.**
* **"Back up everything" remembers where.** The folder chooser starts in
  `~/JamMan-Sicherung` (created if missing) or in the folder used last time, and
  right after a backup it offers to look through the older ones and delete them.
* **The clean-up dialog only lists backups.** It used to list every folder in
  the chosen directory; with a backup accidentally in the home folder it
  offered "Applications" and "Documents" for deletion. Only names starting
  with `JamManSicherung_`, `Sicherung_`, `Hole_` or `vor-ueberschreiben` are
  shown now.
* **The folder chooser returns the folder you selected.** Pressing "Öffnen" on a
  highlighted folder used to return the folder that was open, not the one
  selected — the backup landed one level too high (reported 19.11.2026). The
  highlighted folder now shows in the "Auswahl:" line and is the one returned.
* **The counter counts patches, not rows.** `1 von 12` became `1 von 11` —
  hint rows were being counted.
* Closing gaps keeps the digit width of the folder names (`Patch011` →
  `Patch010`, never `Patch10`), saves the mapping to `~/JamMan-Sicherung`
  first, and afterwards offers to rewrite setlist files.

## 3.116 — 2026-11-18

* **A "Quit" button.** Closes the window cleanly; the start script then closes
  the Terminal as well. Asked for after the app bundle had to go.
* **`JamMan.command` no longer prints shell errors.** A variable had an umlaut
  in its name (`LÄUFT`), which bash does not allow: two "command not found"
  lines appeared at start although the tool ran fine.
* **The dead app bundle is tidied away.** Anyone who had 3.114 gets
  `JamMan Card Tool.app` renamed to `.app.alt` on the next update — it is not
  deleted, only taken out of the way.

* **Windows: the update no longer stops at a read-only file.** A user on
  Windows 10 got `[Errno 13] Permission denied: …\.gitignore` — the package
  contained a development file that Windows had marked read-only. Two changes:
  files whose name starts with a dot are no longer part of the package or of
  the update, and if a target file is read-only the update clears that flag and
  tries once more before reporting an error.

## 3.115 — 2026-11-18

* **macOS: the Terminal closes together with the tool.** `JamMan.command` asks
  Terminal to close its window when the tool quits — macOS asks once whether
  the script may control Terminal. If you decline, the window stays as before;
  the same effect is available as a Terminal setting (Profiles → Shell → "When
  the shell exits: Close the window").

  The app bundle from 3.114 is gone again: macOS refuses to start a downloaded
  and therefore quarantined app that is only ad-hoc signed ("Das Programm …
  kann nicht geöffnet werden"), and notarising it would need a paid Apple
  developer account. A `.command` is what users can actually start.

## 3.114 — 2026-11-18

* **macOS: no Terminal window any more.** Double-clicking `JamMan.command`
  opens a Terminal, and macOS leaves it sitting there after the program ends.
  The package now contains **"JamMan Card Tool.app"** — the same start-up, but
  as an app bundle: only the window opens, and nothing stays behind. The
  bundle looks for a Python with Tk 8.6 or newer exactly like the `.command`
  does, and uses `pythonw` when present. Both guides explain the first-start
  step (right-click → Open) and, for `.command` users, the Terminal setting
  that closes the window when the shell exits.

## 3.113 — 2026-11-18

* **Update works even when the certificates are broken.** On an Intel Mac,
  "Update" ended with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
  certificate` — no certifi, and the usual certificate files did not help.
  Two changes: if the version query fails for that reason it is retried once
  without verification (only a version number is read), and the ZIP download
  falls back the same way — with a plain warning, and with the file size
  compared against GitHub's figure afterwards. A help text names the two
  proper fixes ("Install Certificates.command", `pip install certifi`).
* **The tool starts in the language the system is set to, not the one the
  terminal reports.** On an Intel Mac it came up in English although macOS was
  set to German: the language was guessed from `LANG`/`LC_ALL` only, and an
  English terminal reports `en_US.UTF-8`. The order is now: `JAMMAN_SPRACHE`,
  then the macOS system language (`defaults read -g AppleLanguages`), then
  `LANG`/`LC_ALL`/`LMC_MESSAGES`. `C` and `POSIX` are skipped. A language
  chosen in the window still wins over all of it.
* **README: `dialout` group needs a reboot.** A user on Kubuntu reported that
  logging out and back in was not enough after `usermod -aG dialout`; both
  guides now say so, with `groups` as the check.

## 3.112 — 2026-11-18

* **The tool updates itself.** The daily check no longer only asks: on finding a newer
  release it fetches and installs it, then says that the window has to be closed and
  opened again. A checkbox **"Update beim Start"** turns this off (setting
  `update_auto = nein`); then it asks as before. The button "Aktualisieren" stays.
* **Dialogs speak the chosen language.** In the main window, German texts were
  shown in every language — "keine Karte gefunden" under the English title,
  and 24 messages in the dialogs ("Erst eine Karte wählen.", "Fehler",
  "Die Datei ist nicht lesbar." …). All of them go through `T()` now, and the
  check script looks for German interface texts that bypass the catalogue:
  a literal that is a known German text but is not wrapped in `T()`.
  Reported by the author while testing the English interface.

## 3.111 — 2026-11-18

* **Fix: crash when the USB connection fails.** If connecting to the pedal did
  not work, a late list message could still reach the window, and `anzeigen()`
  read the device name from a device that was no longer there:
  `AttributeError: 'NoneType' object has no attribute 'kopf'`. The window now
  shows "kein Pedal" instead and stays usable. Reported by a user on Ubuntu
  (Kubuntu) whose pedal did not connect — the card reader path was unaffected.

## 3.110 — 2026-11-18

Built on 17–18 November 2026 and tested on Windows 10, Ubuntu and macOS.

### New

* **Firmware from one pedal to another — confirmed on real hardware.**
  On 18 Nov 2026 a JamMan Solo XT was moved from `FirmwareVersion 5376` to
  `5632` by fetching the newer pedal's system files and writing them over
  USB; the pedal then showed **1.06** at start. Two details mattered:
  running files cannot be overwritten (QNX answers „Resource busy“), so
  each file is written next to its target and renamed into place; and
  `usr/attributes/unit_guid.attr` is a per-device id and is never copied.
* **Firmware from one pedal to another (read and write).** Two pedals were
  compared with the memory image: the "firmware" is a set of files on a QNX
  system (`/mnt/nandfs/usr/IOMgr, JamManUI, loopengine, usbserial`,
  `/mnt/nandfs/lib/…`, `JamManSoloXT.xml`, plus `usr/attributes/` on newer
  units). "Sichern ▾ → Firmware dieses Pedals holen …" copies those files to
  the computer; "Firmware übernehmen …" backs up the target pedal's own files
  to `sicherung-vor-firmware-<stamp>/` and then writes them in a safe order
  (libraries first, USB driver last), checking the size after each file.
  `.filetable` is deliberately not copied — it is the filesystem index the
  pedal maintains itself. Command line: `jamman.py firmware-holen <ordner>`
  and `firmware-schreiben <ordner>` (asks for confirmation). An interruption
  can leave the pedal unusable; there is no way back.
* **Memory images leave out /proc, /dev and the SD card.** The first
  comparison of two pedals showed 222 differences, almost all of them from the
  inserted card, the device nodes and the runtime memory.
* **JamLists from JamManager XT.** The manufacturer's JamLists are not a file
  format but rows in `JamManager.db.sqlite` (SQLite): `jamlists(id, color,
  name)` and `loops(…, jamlistid, "Loop #", …)`, where each JamList holds the
  200 slots of the pedal. The tool now reads that library
  ("Setlists ▾ → JamList aus JamManager XT holen …") and writes an existing
  text setlist back as a new JamList ("… als JamList ablegen …"). Command line:
  `jamman.py jamlists <db>`, `jamlist-holen <db> <name> <ziel.txt>`,
  `jamlist-schreiben <db> <name> <quelle.txt>`.
* **Reverse playback and a tempo column.** `phrase.xml` carries `IsReversed`
  and `BeatsPerMinute`; the tool read neither and always wrote 0 / 90. The
  list now shows the tempo ("120 BPM") and marks reversed patches with
  "↺"; the playback choice offers "loop rückwärts" and "single
  rückwärts". Because the pedal reverses on playback while the window
  played the file as-is, "Anhören" now renders a reversed copy with ffmpeg
  (`areverse`) — without ffmpeg it says so and the pedal still plays it
  backwards.
* **Descriptions and tempo.** New button "Beschreibung …" edits the fields the
  pedal stores but the tool never offered: name, artist, genre, rating (0–5),
  tags, description text and **BPM**. Before, every new patch got
  `Genre=Unspecified`, `Rating=0` and `BeatsPerMinute=90` — and the tempo is
  what the pedal's auto-quantize uses.
* **Search and sort in the list.** A search field filters by any text in the
  row (patch name, slot, mode), and the list can be sorted by folder, name,
  duration or reversed name. With 200 slots an unsorted list was hard to use.
* **Update from inside the tool.** The button "Aktualisieren" (and the daily
  "new version" prompt) downloads the latest release, checks that the ZIP
  belongs to the announced version, backs up the files it replaces into
  `sicherung-vor-<old version>/` and puts the new ones in place. The settings
  in `~/.jamman-werkzeug.cnf` are not touched. From the command line:
  `jamman.py aktualisieren`. The running window keeps the old code until it is
  restarted, and the tool says so.
* **Whole folder onto the pedal.** "Schreiben ▾ → Ganzen Ordner aufs Pedal …"
  takes a fetched folder (`Patch01 … PatchNN`) and writes every patch into the
  chosen memory. Occupied slots are backed up first (five deep), unchanged ones
  are skipped, and the result reads "Fertig: n geschrieben, m übersprungen,
  k vorher gesichert". Before, restoring was patch by patch.
* **Setlists follow the moved slots.** After "Plätze aufrücken" the tool asks
  whether the setlist files in the last used setlist folder should be adjusted.
  Each `PatchNN` line is rewritten to the new slot, every changed file is
  backed up next to it (`<name>.vor-aufruecken-<stamp>.txt`), and the status
  line says how many files and lines changed.
* **Close the gaps between slots.** "Schreiben ▾ → Plätze aufrücken …" pulls
  every occupied slot forward so there is no hole (after deleting Patch16 the
  list jumped 15 → 17). The order of the patches stays; only the slot numbers
  change. Each moved slot is backed up first, and the question says that
  setlists pointing at slot numbers will no longer match.
* **Delete a slot over USB.** "Schreiben ▾ → Platz löschen …" asks for the
  slot, names the memory and deletes the patch after a confirmation. Before, a
  wrongly written slot could only be overwritten — and that meant backing up
  the unwanted patch over USB first (17 Nov: 1238 MB).
* **A fetched copy says it is a copy.** Deleting a patch in the main window
  while it shows a fetched copy removed it only there — the pedal kept it, and
  the gap in the list looked like a fault (17 Nov). The window now shows
  "Kopie auf dem Rechner — Änderungen hier wirken nicht auf das Pedal", and the
  delete confirmation names the copy and says the pedal stays unchanged.
* **Confirmation buttons speak the chosen language.** Tk's system dialogs
  labelled their buttons themselves ("Yes"/"No") while the text was German.
  Confirmations now use the tool's own window with "Ja"/"Nein" (or
  "Yes"/"No", "Oui"/"Non", …) — every `askyesno` and `askokcancel` was
  replaced.
* **The chooser stays in front of the USB window.** The USB window keeps
  itself on top, so it jumped over the open dialog (17 Nov). The dialog now
  takes the "always on top" off its parent for the duration and restores it
  afterwards.
* **The tool brings its own dialogs.** Opening files (audio, setlist, level
  log), choosing folders (card, target, patch folder, whole folder, backup) and
  saving (ZIP, setlist, instructions) all use one window: folders on the left,
  matching files on the right, hidden entries skipped, a path field, and a name
  field where needed. Tk's own dialogs and zenity are no longer used; zenity
  appeared behind the window on GNOME, and Tk's dialog was cramped and showed
  every hidden file.

### Fixed

* **Start without a card:** the first thing shown was a dialog asking to pick a
  folder, even when the pedal was reachable over USB. It is gone; the window
  shows the hint, and if a pedal is attached the USB window opens by itself
  with the card *and* the internal memory.
* **Length check before writing audio over USB.** A 1238 MB file
  (~123 minutes) started writing into the 504 MB internal memory; the pedal
  accepts at most 10 minutes per loop. The length is now measured before
  converting and again after, and the write is refused with a clear message.
* **Ejecting with the card in the pedal:** the tool tried to unmount `/media`
  and reported a long polkit error chain. It now finds the real mount point
  (walking up until the device changes); if the card is not in the computer it
  says so plainly, and a failure names only the first reason.
* **Hint dialogs no longer hide behind the window.** The USB window keeps
  itself in front, so its messages (wrong target slot, "press Reload first")
  appeared behind it. They now open as children of the window, with the window
  dropping its "always on top" for the duration.
* **Buttons are locked while a run is active.** Everything except "Abbrechen"
  and "Schließen" is disabled — the three menus, the memory switch, "Neu
  laden", "Ins Hauptfenster holen" and "Verbindung neu aufbauen". (The list was
  collected too early at first, which is why "Verbindung neu aufbauen" stayed
  usable on 17 Nov; it is now collected after the window is complete.)
* **Cleaning up an aborted run no longer races the next one.** After
  "Verbindung neu aufbauen" the aborted run's cleanup question could still be
  pending while the new run was already writing; deleting its `.part` file
  produced `[Errno 2] … .part -> … .wav`. The cleanup is skipped while a run is
  active, and writing a fetched file is retried once if the folder or the
  `.part` file disappears in between.
* **Deleting a slot now says so,** and a failed reload no longer empties the
  list — that is what made the selection look empty after deleting.
* **Same words in the list and in the dropdowns.** The list showed
  "sofort/ende/ohne/klick" while the dropdowns offered
  "instant/finish/silence/kick". Both now use "fade / sofort / ende",
  "ohne / klick", "loop / single"; the internal values are mapped back.
* **List says when audio is missing.** Patches whose audio was not fetched yet
  show "—" in the duration column; a closing line now explains it
  ("N Patches ohne Ton — nur die Beschreibung ist geholt.").
* **Fetching says what it actually did.** The status line ends with
  "Übertragen: N Dateien · unverändert übersprungen: M" — descriptions are
  re-read every time (small), audio only when its size differs.
* **Backups before overwriting are kept five deep.** Each write to an occupied
  slot stores its backup in `vor-ueberschreiben/<slot>/<stamp>/` and removes
  older ones per slot. Before, the slot folder was overwritten, so only the
  very last state existed.
* **Certificate trouble on macOS.** The update check reported
  `CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate` with a
  Python from python.org that never ran "Install Certificates". The check now
  builds its SSL context from `certifi` if present, otherwise from the system
  bundle (`/etc/ssl/cert.pem` and friends), and the message explains the fix
  ("Install Certificates.command" or `pip install certifi`).
* **"GitHub ist nicht erreichbar" now says why.** The message only appeared
  when the update check failed — without a reason. It now names the cause, uses
  a 15 s timeout instead of 8 s, and falls back to the release page's redirect
  when the API is not reachable (rate limit, blocked API): the tag is read from
  `…/releases/latest` and the asset URL built from it.
* **Target slot input is read more tolerantly.** On Windows the field arrived
  as `_Patch17` and was rejected. Whitespace, quotes, a leading `_`, `.` or
  `-`, a trailing slash and a bare number (`17`) are accepted and turned into
  `Patch17`.
* **The chooser copes with folders it cannot read.** On Windows 10 "Eigene
  Musik" (a linked library folder) produced `[WinError 5] Zugriff verweigert`
  and an empty window. The start folder is checked first and the tool falls
  back to Music/home; an unreadable folder during browsing shows a quiet note
  instead of a modal error.
* **Internal fixes found by the checks:** a missing `datei_auswahl()` after a
  cleanup (NameError when opening a file), a typo `vorhanden`/`vorhandene`
  when deleting a slot, and two latent ones in the CLI (`fenster`, `pfad`,
  `wurzel`). `werkzeuge/usb_pruefen.py` now verifies per scope that every name
  used is defined, and that the required functions still exist.

## 3.109 — 2026-11-17

Small things that only show up in use.

### Fixed

* `greenglass.py` started by double-click on Windows: the console closed
  before anything could be read. It now prints a short help with the ports it
  finds and waits for Enter. Errors are shown and the window stays open.
* **Ejecting the card on Linux** asked for a password and then failed. It now
  tries `gio` first (runs in the user session, no password), then `udisksctl`
  with `--no-user-interaction`, then `umount`; a missing program is no longer
  an exception. If nothing works, the message names the reason and suggests
  ejecting in the file manager.
* The main window's empty state now names both routes: card in the computer →
  "Choose card"; card in the pedal → "Pedal über USB", where the card *and*
  the internal memory are reachable. Before it only asked for a card, which
  is wrong when the card is in the pedal.

### Confirmed

* **Windows 10** shows the pedal as "USB Serial Device (COMx)" after
  `pip install pyserial` — no DigiTech driver needed (confirmed 17 Nov 2026).
* **Ubuntu** reads the internal memory over USB (confirmed 17 Nov 2026).
  Reading and writing under Windows is still untested.

### What does not change

No change to how the card or the pedal is written. Anyone on 3.108 loses
nothing.


## 3.108 — 2026-11-16

USB. The pedal's card and its internal memory — read and written.

The JamMan does not appear as a drive over USB; it speaks a serial protocol
("Greenglass", CDC-ACM). That protocol was documented by the
[RenanDiaz/jamman-manager](https://github.com/RenanDiaz/jamman-manager)
project (MIT) and is implemented here independently, in Python, without
extra libraries on macOS and Linux (Windows needs `pyserial`).

### What is new

* **"Pedal über USB"** — its own window: pick internal memory or SD card,
  see the patches with their names, fetch everything into the main window.
* **Writing**: a whole patch folder onto the pedal, or an audio file
  (WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, WMA, AIFF, CAF) which is converted
  to 44.1 kHz / 16 bit / stereo and becomes a patch. An occupied slot is
  backed up to the computer first, then cleared.
* **Backup as ZIP**, including the internal memory — WAVs are stored
  uncompressed, which makes the archive much faster.
* **"Sicherungen aufräumen"**: every run with date and size, delete what is
  no longer needed. The backup used by undo is marked and asked about
  separately.
* **Undo**: the last write can be taken back from its backup.
* **Setlists**: select patches (⌘-click, ⇧-click, "select all"), save as a
  text file, fetch a setlist back onto the computer.
* **Everything fetched is remembered**: `<target>/intern/...` and
  `<target>/karte/...`, so a second run only fetches what is missing.

### What changed

* The window title says what is open: card, internal memory, or a copy
  taken from the pedal. A note warns when a copy is open instead of the card.
* "Reload" finds the card again if the remembered folder has disappeared.
* The USB window is built from a few buttons and four menus instead of
  fifteen buttons; confirmation dialogs come to the front; "Cancel" stops
  within a fraction of a second.
* Fixed: two threads on the same serial port could silence the pedal — only
  one speaker at a time now, and a stalled request recovers by itself.
* Removed: the footswitch assignment and "follow the pedal" — the Solo XT
  stores those values but does not act on them.

### What does not change

The card itself is handled as before. Anyone on 3.107 loses nothing, and
nothing is written without backing it up first.


## 3.107 — 2026-11-16

One name everywhere.

The tool called itself "JamMan-Werkzeug" in `--version`, in the launchers and
in a note inside the FFMPEG instructions, while the folder and the project are
named `jamman-card-tool`. Unpacking the ZIP showed one name, starting the tool
showed the other.

All five places now say **JamMan Card Tool**.

* `--version`, `JamMan.command`, `JamMan.sh`, `JamMan.bat` and
  `FFMPEG-INSTALLATION.txt`: "JamMan-Werkzeug" → "JamMan Card Tool"
* No behaviour changes. Anyone on 3.106 loses nothing, and the card is not
  touched.

## 3.106 — 2026-11-13

Fix: several places still spoke German in every language.

* 54 `SystemExit("Fehler: …")` calls never went through `T()`: `play` on an
  English window answered "Fehler: --patch fehlt."
* all 19 prompts of the text menu (`input()`) were German, including the
  "Auswahl:" line the French menu showed.
* the positional argument appeared as `BEFEHL` in the usage line of every
  language; it is now translated (`COMMAND`, `COMMANDE`, `ORDEN`, `命令`).
* the yes/no questions only accepted `j`; `y`, `ja` and `yes` work as well
  (`o` still does in French, `s` in Spanish — any of j/y/ja/yes/o/s counts).

All texts are translated into English, French, Spanish and Chinese; the four
catalogues hold 469 entries each. Audited with a script: every `T()` string
has an entry, no `print`, `input` or `SystemExit` carries untranslated German,
and the placeholders match in all five languages.

Also corrected: the status table in both READMEs still said Windows 10 and
Linux were "not tested yet". They have been tested (Windows 10 and Ubuntu
Studio by the author, Kubuntu by the user with the Stereo), and the table now
says what "tested" means.

## 3.105 — 2026-11-13

Fix: the translations were behind — in two ways.

First, the catalogues for `fr`, `es` and `zh` had 211 entries against 251 in
English, so everything added since 3.101 (command line options, the new
dialogs) appeared in English on a French, Spanish or Chinese window.

Second — and larger — 153 German texts were passed through `T()` without
being in any catalogue: the command line output, the column headers of the
patch list, the whole self test. A non-German window showed German there.
All 153 are translated, and not only into English: French, Spanish and
Chinese have their own text as well. The four catalogues now hold the same
411 entries.

Also corrected: the user who confirmed 3.102 on a real JamMan Stereo uses
**Kubuntu**, not "Linux" in general. CHANGELOG, both READMEs and the landing
page say so.

## 3.104 — 2026-11-13

Feature: the window tells you when a newer version exists.

On opening, the tool asks GitHub once a day for the latest release. If that
release is newer than the running copy, it offers to open the download page.
The question runs in a background thread, so the window never waits for the
network; without a connection nothing is shown and nothing is reported.

No data is sent beyond the plain request to `api.github.com` — no version of
the card, no file names, no user name. The check can be switched off: put
`update_pruefung=nein` into the settings file (its path is shown by
`jamman.py selbsttest`).

## 3.103 — 2026-11-13

Fix: the window no longer disappears when there is no card.

Before, `gui()` looked for the card **before** building the window. Without a
card that ended in a `SystemExit` — and with it the program: on Windows the
window flashed up and closed at once, on Linux apparently nothing happened at
all. A user hit exactly this when he plugged the pedal in by USB instead of
putting the card into a reader.

Now the window is built first. If no card is found, it says so in plain words
— "take the microSD out of the pedal and put it into a card reader; with the
USB cable attached it does not show up as a drive" — and offers to pick the
folder by hand.

Also: `JamMan.bat` and `JamMan.sh` now wait for a key press when the program
ends with an error, so the message can be read instead of vanishing with the
console window.

## 3.102 — 2026-11-12

Feature: the tool now also opens cards from the **JamMan Stereo**.

A user in the Bassic forum reported the card being rejected with "that is
not a JamMan card". The reason was the device folder: the Solo XT writes
`JamManSoloXT`, the Stereo writes `JamManStereo`. Below that the layout is
the same (`Patch01` … `Patch99`, each with `PhraseA/phrase.xml`,
`phrase.wav` and `patch.xml`).

Now both names are accepted — and any other spelling with different case.
The folder that was found is used for every later step, so nothing else had
to change. The message when no card is found names both folders.

**Tested:** with a rebuilt Stereo card (folder structure, listing, reading
the patch name) and with a Solo XT card.

**Confirmed on a real pedal (2026-11-13):** the user who reported the
problem ran 3.102 on his JamMan Stereo under Kubuntu and wrote back:
"Funktioniert, top." The author tested the tool itself on Ubuntu Studio and
Windows 10. Writing to a Stereo
card still deserves a full copy of the card first — but it is no longer
untested ground.

## 3.101 — 2026-11-11

Fix: `pegel-umkehren` / "Pegel zurückrechnen" no longer crashes when the
chosen file is not a level protocol. Before, `lies_pegelprotokoll()` opened
the file with `encoding="utf-8"` and no guard, so a WAV, a missing file or a
folder ended in a Python traceback
(`UnicodeDecodeError: 'utf-8' codec can't decode byte 0x92 …` /
`FileNotFoundError`). Now it reports in the same plain language as the rest
of the tool and the window shows a dialog:

* `Fehler: '<pfad>' gibt es nicht.`
* `Fehler: '<pfad>' ist ein Ordner, keine Datei.`
* `Fehler: '<pfad>' ist kein Pegelprotokoll (keine lesbare Textdatei).`
* `Fehler: '<pfad>' darf nicht gelesen werden.`

Checked on 11.11.2026 with four cases (WAV, missing file, folder, README)
and with a real protocol from `alle-normieren`: error cases give a sentence,
the real protocol still reverses 24 patches.

## 3.100 — 2026-11-11

README: the splitting feature now has its own section in both languages
("Das 10-Minuten-Limit umgehen: Datei teilen" / "Working around the
10-minute limit: splitting a file"). It explains

* why the pedal stops at 10 minutes (manual p. 60) and what happens to
  longer files,
* the three ways out: `--kuerzen`, `--laenge`, and **splitting**,
* how splitting works: cuts at pauses (±90 s around the calculated
  point), `--einfach` for even cuts, sample-exact seam, the pause at the
  seam removed by default, `--mit-pause` to keep it,
* that each part must stay under 10 minutes and is shortened if needed,
* how to play the parts gaplessly on the pedal: FS3X, cue at least 2 s
  before the end (manual p. 57).

Every claim was checked with a 25 s file containing a 1 s pause:
default → 12.0 s + 12.0 s (pause removed), `--mit-pause` → 13.0 s + 12.0 s,
`--einfach` → 12.5 s + 12.5 s, names "Probe 1/2" and "Probe 2/2".

## 3.99 — 2026-11-11

The donation link is live.

* `SPENDEN_LINK` points to the PayPal hosted button
  `PEJEFMSKUPA8C` (euros). The "Donate" / "Spenden" button in the window
  opens it; verified by intercepting the browser call.
* README (de/en), `SPENDEN.txt` and `DONATE.txt` carry the real link
  instead of the placeholder.
* Checked while logged out: the page offers "Donate with PayPal" and
"Donate with Debit or Credit Card", so a donor does not need a PayPal
account.
* The email-based donate URL (`?business=anna@…`) does **not** work — PayPal
  answers "Diese Organisation kann zurzeit keine Spenden akzeptieren".
  The hosted button does, so that is what is published.

## 3.98 — 2026-11-10

Ready for publication:

* "Spenden" / "Donate" button in the window. The link lives in one place
  (`SPENDEN_LINK` in `jamman.py`); while it is empty the button explains
  where to put it instead of opening a dead link.
* `SPENDEN.txt` and `DONATE.txt` describe the two places to fill in.
* README (de/en): screenshots in `docs/`, section "Unterstützen" /
  "Support" with the placeholder.
* `GITHUB-VEROEFFENTLICHEN.txt`: the exact steps — create an empty
  repository, add the remote, push.
* A local git repository with two commits, so publishing is one command.
  Tracked: 13 files plus three screenshots; no audio, no card data.

*Removed on 2026-11-13: `GITHUB-VEROEFFENTLICHEN.txt` (a note on how to
publish the repository) is no longer in the repository — it stays in the
history and in the author's notes.*

## 3.97 — 2026-11-10

English command names and a translated `--help`:

* Every command has an English alias: `list`, `backup`, `import`, `play`,
  `delete`, `move`, `swap`, `place`, `restore`, `normalize`,
  `undo-level`, `split`, `change`, `clean`, `eject`, `names`, `selftest`.
  The German names keep working.
* `--help` follows `--sprache` — the parser is built after the language is
  read from the command line, so the option texts and the command list are
  translated too.
* README (de/en): table of all command names.
  Verified: German help unchanged, `--help --sprache en` fully English,
  all ten English aliases run on the test card.

## 3.94 — 2026-11-10

Pre-release review, findings fixed:

* Two strings in `teilen` were still German ("Cut 1 at …: no pause
  nearby …"); the cut reason is now translated as a value too.
* `CHANGELOG.md` and `LICENSE` were missing in the handover folder — added.
* `__pycache__` removed from the package.

Checks run: format placeholders identical in all five catalogues (0
deviations), every command as a dry run on a throwaway card, the window
opened and operated in all five languages, tool started from a fresh
directory, handover copy identical to the source.

## 3.93 — 2026-11-10

French, Spanish and Chinese now cover the whole catalogue — window,
dialogs and command line (146 texts per language, all five catalogues
have the same keys).

The translations were added by pairing them with the key list of the
English catalogue by position and asserting the count, so no German key
had to be retyped (and no key can silently mismatch).

Verified: `sichern` and `selbsttest` in French, Spanish and Chinese; the
window reads "Carte mémoire du JamMan Solo XT" with all buttons
translated. As before, French, Spanish and Chinese are **not** reviewed
by native speakers — the window says so.

## 3.92 — 2026-11-10

Command line in English:

* All `print` strings are wrapped with `T()` — done by a script that uses
  the syntax tree, so 72 plain and 110 formatted strings were wrapped
  without editing them by hand. `T()` keeps leading indentation, so the
  columns of the tables stay intact.
* English texts for the report of `selbsttest`, the commands
  (`sichern`, `aufspielen`, `loeschen`, `menu`, …), the dry-run notes and
  the error hints.
* Inline values are translated too ("as template" instead of
  "wie Vorlage", "← will be converted").
  Verified: `sichern`, `aufspielen`, `loeschen`, `selbsttest` and `menu`
  in English; German unchanged.
* French, Spanish and Chinese fall back to English on the command line.

## 3.89 — 2026-11-10

Dialogs in English:

* `uebersetzt_mb()` wraps `tkinter.messagebox`, so the 54 dialog calls did
  not have to be edited one by one — title and text are translated when
  the dialog opens.
* Status line: static messages are translated too (the `StringVar.set`
  method is wrapped). Messages that contain a patch name stay German,
  because they are assembled at runtime.
* English texts for the dialog titles and bodies, e.g. "Backup",
  "Import", "All patches are brought to the same loudness: …".
  Verified: "Normalise all" opens with English title and text, and
  "next" at the end of the list shows "end of list reached".
* French, Spanish and Chinese fall back to English in dialogs.

## 3.87 — 2026-11-10

* Provisional-translation note: when French, Spanish or Chinese is
  selected, the window shows a line in that language
  ("翻译为初步版本，欢迎指正。" / "Traduction provisoire — corrections
  bienvenues." / "Traducción provisional — se agradecen correcciones.").
* README (de/en) explains the three ways to choose a language and states
  which translations are checked (English) and which are not
  (French, Spanish, Chinese) — with a pointer to the `TEXTE` dictionary
  for corrections.

## 3.86 — 2026-11-10

* Language chooser in the window (top right, "Sprache / Language"): the
  five languages by their own names. Choosing one saves it and reopens
  the window in that language — no restart of the program needed.
* The choice is kept in `~/.jamman-werkzeug.cnf` and used on the next
  start; `--sprache` still overrides it for one run.
  Verified: switching from German to English reopens the window with
  "JamMan Solo XT memory card" and stores `sprache=en`.

## 3.85 — 2026-11-10

First stage of internationalisation:

* Language table with five languages: German (source), English, French,
  Spanish, Chinese. `T()` looks up the chosen language, falls back to
  English and then to the German original — a half-finished translation
  never leaves an empty label.
* `--sprache de|en|fr|es|zh`; without it the system language is used
  (`JAMMAN_SPRACHE`, `LC_ALL`, `LC_MESSAGES`, `LANG`), default German.
* The window is translated: title, list header, buttons, checkboxes,
  settings row, drop-downs (the stored value stays German, only the label
  is translated), footer hint.
* Still German: dialog texts, status messages and the whole command line.

## 3.80 — 2026-11-10

* Optional backup on start: checkbox "beim Start sichern" (kept in
  `~/.jamman-werkzeug.cnf`, works on all three systems). Before the
  window opens, the card is copied to `~/JamMan-Sicherung/Start_<time>/`
  — but only if it changed since the last backup. Change is detected by
  file count, total size and newest modification time.
* The three newest start backups are kept, older ones are removed (only
  folders named `Start_…`; the rest of the backup folder is untouched).
* Verified with the test card: first start copies 8 patches, second start
  reports "unchanged, nothing to do", rotation deletes the two oldest
  and leaves foreign backups alone.

## 3.77 — 2026-11-10

* The cue button now sits in a coloured frame instead of relying on
  `highlightbackground`. Windows draws Tk buttons natively and ignores
  that option, so the button changed its text but showed no colour there.
  A plain frame works on macOS, Windows and Linux alike.
  Verified: frame turns amber while a patch is cued and returns to the
  window colour afterwards.

## 3.76 — 2026-11-10

* New command `namensform`: checks whether the patch folders are spelled
  the way the device spells them (`Patch01`) and can rewrite them —
  dry run by default, `--schreiben` applies, and a mapping file is
  written so every rename can be undone. Case-only renames go through a
  temporary name because FAT32 would otherwise ignore them.
* On the restored card no rename was needed: it now shows `Patch01`.
  The all-caps spelling seen right after restoring was a passing state
  (8.3 name before the long name was indexed).

## 3.75 — 2026-11-10

* Patch folders are recognised regardless of case. FAT32 stored the first
  folder of a restored card as `PATCH01`, and the case-sensitive match
  skipped it — the card showed 31 of 32 patches.
* Verified after restoring a card from backup: 32 patches, all 96 files
  (patch.xml, phrase.xml, phrase.wav) present, sizes identical to the
  backup.

## 3.74 — 2026-11-10

* Read errors from the file system are now named as such instead of
  showing an empty card. Ubuntu Studio reported `Input/output error` for
  every patch folder: the card was mounted, its directory could be
  listed, but `stat()` on the entries failed. That is a damaged card or
  mount, not a program error.
* `selbsttest` prints what to do (insert the card in the pedal, `dmesg`,
  `fsck.vfat -n`, `exfatfsck`), the window shows a dialog once and the
  terminal gets a short warning.

## 3.71 — 2026-11-10

* New `ist_ordner()`: a patch folder is accepted when the file system
  reports it as a directory **or** when it contains `patch.xml` /
  `PhraseA/phrase.xml`. Ubuntu Studio reported the patch folders of a
  JamMan card as "Datei", so every patch was skipped and the card showed
  0 patches although it was found.
* The self test prints mode bits and whether `patch.xml` is readable for
  the first entries of the chosen folder — enough to tell a broken mount
  from an unusual file system.

## 3.70 — 2026-11-10

* `selbsttest` no longer crashes when the chosen card shows 0 patches;
  instead it prints the first twelve entries of that folder with their
  kind (folder/file). This is how to see why a card is found but its
  patches are not (Linux report: card found with 32 patches, then
  "Patches: 0").
* `selbsttest --karte <pfad>` now actually uses the given path.

## 3.68 — 2026-11-10

* `selbsttest` now prints every place it searched and what it found there
  — the quickest way to see why a card is not picked up on Linux.
* The card dialog starts in the folder where removable media live
  (`/media/<user>` on Linux) instead of somewhere in the home directory.
* Selecting the `JamManSoloXT` folder itself is accepted, not only the
  drive root.

## 3.65 — 2026-11-10

* The window shows a bar when ffmpeg is missing (Windows/Linux):
  "MP3 und M4A brauchen ffmpeg – [Anleitung]". The guide opens in its own
  window, with a "Text kopieren" button. On macOS the bar never appears —
  `afconvert` is built in.
* The same guide is a file: `FFMPEG-INSTALLATION.txt` (Windows and Linux
  steps, how to check it worked).
* The text menu prints the hint at start as well.
* Verified by forcing "Windows without ffmpeg": the bar appears, the
  guide window opens with the winget command.

## 3.64 — 2026-11-10

* MP3/M4A on Windows: the import needs a decoder. `ffmpeg` is now looked
  up in more places (PATH, next to the program, an `ffmpeg` subfolder,
  the WinGet link folder, `C:\ffmpeg\bin`, Program Files) and the error
  message says exactly how to install it (`winget install --id
  Gyan.FFmpeg`) or that dropping `ffmpeg.exe` next to the program is
  enough. Plain WAV works without ffmpeg.
* `selbsttest` reports whether ffmpeg was found.
* Verified: import of an MP3 and an M4A through the ffmpeg path (macOS
  with the Windows branch forced) produces 44.1 kHz / 16 bit / stereo
  patches.

## 3.61 — 2026-11-10

Windows 10: cueing worked once and then stopped. Three causes, all fixed:

* The cue was cleared only on one of the two paths that switch to the
  cued patch, so after the first switch it stayed set and blocked the
  next one.
* The watcher compared the sounding patch with the cue on every tick and
  jumped immediately when they differed. Removed — the switch is planned
  by time and by the end of playback.
* Temporary files were created with `tempfile.mkstemp` and the handle was
  never closed. Windows then refuses to delete them and can keep them
  locked, which breaks later cue chains. New helper `neue_tempdatei()`
  closes the handle immediately.

Verified on macOS with real audio and with simulated Windows playback
(no process handle): three cues in a row, each switching at the end of
the running patch (2.7–2.9 s for 3 s patches).

## 3.59 — 2026-11-10

Portability pass towards Windows and Linux:

* Playback state no longer depends on a process handle. Windows
  (`winsound`) returns no process, so `.poll()` crashed; the state is now
  computed from the known start time and durations when no process exists.
* Card discovery searches all drives on Windows and `/media`, `/run/media`
  (also one level deeper, e.g. `/media/<user>/JAMMAN`) and `/mnt` on Linux.
* Fonts per system: Menlo (macOS), Consolas (Windows),
  DejaVu Sans Mono (Linux) — "Menlo" does not exist on the other two.
* `selbsttest` reports system, audio program, card roots and font.
* README: table of what is verified and what is not (Windows/Linux
  untested, Android not possible).

## 3.57 — 2026-11-10

* A cue survives seeking in the waveform. Clicking into the waveform
  stops and restarts playback, and the stop routine cleared the cue — so
  the button went back to "vormerken". Stopping now takes a flag
  (seeking keeps it, the Stop button clears it).
* The switch time is re-planned after every seek, so the cued patch still
  arrives at the right moment.
  Verified: cue 4, seek to 15 %, button stays "vorgemerkt 4", patch 4
  plays 2.6 s later; Stop button clears the cue.

## 3.55 — 2026-11-10

* Buttons that need a selected patch are greyed out while nothing is
  selected: Anhören, Umbenennen, Platz ändern, Tauschen, Löschen,
  Datei teilen, ▲ nach oben, ▼ nach unten, Einstellungen anwenden —
  no more "first select a patch" dialogs.
* Card-wide actions stay active (Karte wählen, Alles sichern, Alle
  normieren, Karte auswerfen, Sicherung zurückholen, Pegel zurückrechnen,
  Neu laden) — they need no selection.

## 3.54 — 2026-11-10

* The "vormerken" button shows when something is cued: amber frame and
  label "vorgemerkt 7". Back to normal as soon as the cued patch plays,
  or when playback is stopped.
  Note: macOS draws Tk buttons natively and ignores `background`, so the
  highlight uses `highlightbackground` (a coloured ring).

## 3.53 — 2026-11-10

* The window is sized from the space its controls actually need. The last
  row needed 1162 px but the window was fixed at 1080, so the buttons on
  the right were cut off. Measured after the fix: 1186 px, no button
  clipped. Labels shortened: "automatisch weiter", "▲ 5" / "▼ 5".
* Sizing happens after the layout is built — before that the measurement
  is meaningless (first attempt returned the minimum size).

## 3.52 — 2026-11-10

* Cueing no longer interrupts the running patch. It used to rebuild and
  restart the audio stream on the button press, which was audible as a
  short dropout. Now the chain for the cued patch is prepared in the
  background (file work only) and the switch happens at the end of the
  running patch, timed by the known patch length.
  Measured: cue pressed 0.3 s into a 3.0 s patch → cued patch at 2.97 s.

## 3.48 — 2026-11-10

* Cueing no longer depends on the audio process state. It asked
  `laeuft()` (is the player process still alive?) — and if that answered
  no while sound was still coming, the cue was remembered but never
  applied: the patch played to its end and nothing followed.
* The cue is now applied by a small watchdog that fires when the sound
  really stops, whatever the process state says.

## 3.47 — 2026-11-10

* Cueing now also works when "danach nächster Patch" is off and when the
  chain could not be built: the cued patch is started when the current
  one ends.
* If cueing fails, the status line says so instead of staying silent
  (a silent failure was the likely reason the cue seemed to do nothing).
* Playback, seeking and cueing all use the same chain builder now.
  Verified: patch playing → cue another → cued patch starts at the seam
  (with auto-advance on and off).

## 3.45 — 2026-11-10

* Cueing, like the FS3X on the pedal: type a slot, press "vormerken"
  (or F2). That patch plays when the current one ends; afterwards the
  patches that follow **it** continue. Verified: patch 1 playing, cue 3
  → 1 → 3 → 4.
* Chains are built in one place now (`kette_bauen`), used by playback,
  seeking and cueing.

## 3.43 — 2026-11-10

* The button next to "Platz" is now called "nächster" and means exactly
  that: with a number in the field it jumps there, with an empty field it
  goes to the next patch (like the pedal's UP switch).
* Escape now clears the slot field and the typed buffer everywhere, not
  only while the field has focus.

## 3.42 — 2026-11-10

* Typing a slot number now works wherever the keyboard focus happens to
  be, and the number appears in the "Platz" field while you type. Enter
  jumps to it, Esc clears. The field is therefore no longer a dead end if
  it does not take focus on a given Mac.

## 3.41 — 2026-11-10

* Typing in the input fields works again: a click anywhere in the window
  used to pull the keyboard focus back to the list. Entry fields are now
  excluded.
* Second way to pick a slot, like on the pedal: with the list focused,
  type the number (status line shows "Platz 31"), press Enter to jump,
  Esc to clear. This does not depend on any field having focus.

## 3.38 — 2026-11-10

* Pick a patch by number, like pressing a slot on the pedal: field
  "Platz" plus button "gehen" (or Enter) selects that slot and starts it.
  An unknown slot says so instead of doing nothing.
  Verified: patch 31 → 1 (two long halves), 1 → patch 1,
  99 and 0 → message.

## 3.37 — 2026-11-10

* Fix: while a chained playback was running, clicking into the waveform
  left the playhead at the far left and it could not be moved. Two
  causes: the watcher start was scheduled more than once (several loops
  overwrote the marker), and the marker position ignored the offset of
  the first chain segment (the part that starts at the click point).
* Only one watcher runs now (`plane_beobachten` cancels the previous
  one), and each chain segment carries its offset.
  Measured: click at 30 % while playing → playhead at 315 of 1037 px,
  then creeping on; one audio process.

## 3.35 — 2026-11-10

* The scroll buttons move five rows per press instead of a whole screen
  (which jumped almost to the end of the list). Their labels say so:
  "▲ 5 Zeilen" / "▼ 5 Zeilen". The PageUp/PageDown keys still move a full
  page.
* Row height is measured from the list instead of assumed, so page
  arithmetic no longer depends on a guessed constant.

## 3.34 — 2026-11-10

* Playing along with "danach nächster Patch" no longer drags the list
  away under your fingers: the auto-follow scroll pauses for five seconds
  after you have scrolled yourself (wheel, page buttons, scrollbar).
* Page step is four rows shorter than a screen, so the last row is not
  already reached with the first press.

## 3.33 — 2026-11-10

* The red playhead is set the moment playback starts (and on every seek),
  and is carried along while chained playback moves from patch to patch.
  Before, it could vanish for a moment at the start or when the waveform
  was redrawn for a new patch. Measured: 87 samples over 9 s, playhead
  present in all of them, also right after a seek.

## 3.31 — 2026-11-10

* Fix: after clicking into the waveform the selection jumped to the next
  patch immediately. The time base was the patch position instead of the
  start of the chained file, so the "which patch is sounding" mapping was
  off by the seek position.

## 3.30 — 2026-11-10

* Seeking in the waveform also continues gaplessly: the excerpt from the
  click point is concatenated with the following patches into one file,
  instead of playing a lone excerpt and starting the next patch after it
  (that was still audible with the first of two long patches).

## 3.29 — 2026-11-10

* Fix `KeyError: 'abschnitte'`: seeking in the waveform called the
  watcher, which expected the chain state that only existed after a
  normal playback start. The key now exists from the beginning; seeking
  resets it. Also checked every state key used in the window against the
  initialisation — no other gap.

## 3.28 — 2026-11-10

* Why a short gap was still audible: the chain was capped at 10 minutes,
  so two long halves (6:39 + 8:59) never got chained and the player fell
  back to starting them one after another. A chain now always contains at
  least two patches (budget 30 minutes / 8 patches).
* Splitting now removes the pause at the seam by default: part 1 ends
  where the sound stops, part 2 begins where it resumes — so the two
  halves join music-to-music. `--mit-pause` keeps the old behaviour.

## 3.27 — 2026-11-10

* "danach nächster Patch" is now truly gapless: instead of starting one
  player per patch (which left a 200–400 ms hole), the tool concatenates
  the current patch and up to eight following ones into one temporary
  file and plays that as a single stream. Measured: 4 × 3 s patches play
  for 12 s with no interruption, the selection follows the audible patch.

## 3.26 — 2026-11-10

* The `<<ListboxSelect>>` binding was lost when the list went back to a
  Listbox, so the waveform only refreshed after a mouse press. Restored.
* Verified: click a row, click at 92 % of the waveform, and when that
  excerpt ends the next patch starts by itself.

## 3.25 — 2026-11-10

* New checkbox "danach nächster Patch": when a patch ends by itself, the
  next one is selected and played — handy for checking a split in one go.
  At the end of the list it says so and stops.

## 3.24 — 2026-11-10

* Back to a plain Tk Listbox. It brings its own mouse-wheel handling
  (`tk::MouseWheel`), which is why the wheel worked before 3.13 and not
  after the canvas rewrite. The drop indicator is now a marked row
  "═══ hier ablegen ═══" inserted between rows.
* Page buttons "▲ Seite" / "▼ Seite" under the list (computed in rows —
  Tk's own "pages" unit moves only half a page).
* Own wheel/arrow bindings removed, so nothing counts twice.

## 3.22 — 2026-11-10

* Page buttons "▲ Seite" / "▼ Seite" under the list, so scrolling never
  depends on the mouse wheel. A test with real scroll events sent to the
  window left `/tmp/jamman-gui.log` empty — the events do not arrive at
  the Tk application on this macOS.

## 3.21 — 2026-11-10

* Root cause of "the wheel does nothing in the window": Tk ships a wheel
  class binding for `Listbox` (`tk::MouseWheel …`) but none for `Canvas`.
  Since the list is a canvas now, wheel events did nothing. The same
  mechanism is now installed for the canvas class, plus a direct binding
  that stops further propagation so nothing counts twice.
* A small diagnostic log is written to `/tmp/jamman-gui.log` for every
  wheel event, to see whether the event reaches the window at all.

## 3.19 — 2026-11-10

* Mouse wheel: bound once at the window (Tk passes child events up), so a
  notch scrolls exactly three units instead of counting twice. Also
  handles `Button-4`/`Button-5`, and the arrow keys scroll a row.

## 3.17 — 2026-11-10

* Scrolling is back to Tk's own mechanism: the list canvas gets a real
  scroll region, the scrollbar calls `yview` directly, the wheel and the
  keys use `yview_scroll`. The hand-written scrolling introduced in 3.13
  (its own "first visible row" bookkeeping) did not work reliably with
  real mouse input — measured: scrollbar 0.5 → top row 16, bottom → last
  row, PageUp → two rows up.
* All rows are drawn now (32 patches = 64 canvas items, negligible).

## 3.16 — 2026-11-10

* Mouse wheel scrolls three rows per notch instead of one (one felt like
  it would never reach the end) and is accepted anywhere in the window.
* Keys: PageUp/PageDown for a page, Home/End for the first/last patch.
* The first paint after start showed only one row because the list had
  no height yet; it now draws properly.

## 3.15 — 2026-11-10

* Fix: the window became sluggish (beachball) — the waveform took over
  two seconds for a 75 MB loop because it built a Python list of every
  sample. It now samples 20 ms per pixel column and uses C-level
  max/min: 3.2 s → 0.03 s for a 15-minute loop.
* The waveform is cached per patch (no recomputation on every reload),
  and while playing only the playhead is moved instead of redrawing 900
  lines every 300 ms.
* Resize events are debounced, so dragging the window edge no longer
  triggers a redraw storm.

## 3.14 — 2026-11-10

* Fix: the scrollbar did nothing after the list became a canvas (a canvas
  has no scrolling region over virtual rows). It now moves the list
  itself — dragging, page steps and the thumb position all work.

## 3.13 — 2026-11-10

* The list is now drawn on a canvas instead of a Tk Listbox. That makes a
  real insertion line possible: while dragging, a red line shows where
  the patch will land, between two rows.
* Auto-scroll while dragging: near the top or bottom edge the list
  scrolls by itself, so a patch can be moved across the whole card.
* Mouse wheel scrolls the list.

## 3.12 — 2026-11-10

* Dragging patches now shows what is happening: the mouse pointer turns
  into a move cursor, the row under the pointer is highlighted as the
  drop target, and the status line names the patch being dragged and the
  slot it would land on. Before it was blind flying.

## 3.11 — 2026-11-10

* Fix: splitting crashed with `IndexError` on 24-bit WAV files (and on
  anything the pause search could not read). The file is now converted to
  44.1 kHz/16 bit **first**, then analysed and cut; if no usable pause is
  found the tool says so and cuts evenly.
* Verified: 24-bit WAV, MP3 and FLAC all split correctly.

## 3.10 — 2026-11-10

* The waveform strip is refreshed whenever the list reloads. Before, a
  deleted patch left its waveform behind until another row was clicked.

## 3.9 — 2026-11-10

* Waveform strip under the list: shows the envelope of the selected
  patch, with a red playhead while playing. Click into it to jump — since
  `afplay` cannot seek, a temporary excerpt from that point is written
  and played, and removed afterwards.

## 3.8 — 2026-11-10

* Splitting looks for a musical cut: the tool measures the level in
  100 ms windows, finds quiet passages (below 35 % of the average) and
  cuts at the **end** of such a pause — that is where the next musical
  section starts. If no pause is found near the arithmetic boundary it
  says so and cuts by time.
* New button "Datei teilen" with a "Teile" field and a checkbox
  "an Pausen schneiden" (on by default). Command line: `teilen --wav …`
  (`--einfach` for even cuts).

## 3.7 — 2026-11-10

* New: split a long file into several patches — `teilen --wav FILE --teile 2`
  or `--laenge 450`. The parts are cut sample-exactly (verified: the two
  halves joined again are bit-identical to the whole), named
  "Name 1/2", "Name 2/2" and placed on free slots.
* Each part is checked against the 10-minute limit, so a split that
  still ends up too long is refused.

## 3.6 — 2026-11-10

* Trimming: a file longer than 10 minutes can be cut right away instead
  of only being refused. `--kuerzen` (to 9:59), `--laenge 480` (to a
  chosen length), or the window asks "Auf 9:59 kürzen?" before importing.
  The cut is seamless — nothing is inserted, no gap — and the last 12 ms
  are faded out so the seam does not click.

## 3.5 — 2026-11-10

* The pedal accepts at most **10 minutes per loop** (manual, page 60:
  "Max. Individual Loop Time: 10 minutes (stereo)"). Longer files used to
  be written to the card, were ignored by the pedal and could disappear
  from the card afterwards. Import now refuses anything longer and says
  why; `--trotzdem` overrides it.
* Patches over 10 minutes are marked in both list views.

## 3.4 — 2026-11-10

* Fix: files converted from M4A got a WAVE_FORMAT_EXTENSIBLE header
  (0xFFFE, 40-byte fmt chunk) from afconvert — the pedal does not
  recognise such patches (seen on one patch of the card). The tool now
  rewrites the header to plain PCM (format 1, 16-byte fmt) while leaving
  the audio data untouched. MP3 sources were unaffected, which is why
  only one of two imports failed.

## 3.2 — 2026-11-10

* Import accepts more formats: WAV, AIFF/AIFC, CAF, MP3, M4A/AAC, FLAC,
  OGG/Opus. Anything that `afconvert` can read works without ffmpeg;
  ffmpeg is only a fallback.
* Measuring works without ffmpeg too: `afinfo` (part of macOS) is asked
  when ffprobe is missing. Before, every non-WAV import failed on a Mac
  without Homebrew.
* A conversion that ends in silence is rejected and the half-made patch
  is removed, instead of leaving a dead slot on the card.

## 3.1 — 2026-11-10

* The option row said "Beim Aufspielen:" although the same fields also
  change an existing patch. It now says "Einstellungen:".

## 3.0 — 2026-11-10

* Level protocol: every level change writes a small text file
  `Pegelprotokoll_<date>_<reason>.txt` with folder, name, factor and the
  loudness before/after. That makes a level change reversible without
  the big backup: `pegel-umkehren --protokoll DATEI` divides the factor
  out again. Button "Pegel zurückrechnen".
* Tested: −4,23 / −9,24 / −15,26 dB → normalized → reversed → exactly the
  same three values again.

## 2.9 — 2026-11-10

* New: "Alle normieren" brings every patch to the same loudness (RMS
  −18 dBFS, peaks capped at −1 dBFS) — for a live set without level
  jumps. It backs up the whole card first.
* New: `zuruecknehmen --alle --aus ORDNER` restores every patch from a
  backup folder (undo for the batch action).
* Safety: a path given explicitly is now used **only**; the window no
  longer falls back to another drive when it cannot open it. That
  fallback is what made a test run hit a real card.

## 2.8 — 2026-11-10

* Closing the window now stops playback. Before, the audio player kept
  running as an orphan process after the window was gone.
* The same cleanup runs when the programme ends any other way
  (atexit), e.g. Cmd+Q.

## 2.7 — 2026-11-10

* The play button no longer renames itself to "Stopp" while playing —
  that produced two "Stopp" buttons side by side. It stays "Anhören" and
  starts the selected patch (stopping whatever was playing first).

## 2.6 — 2026-11-10

* Two things were called "Stopp": the stop-mode selector and the button
  that stops playback. The selector is now "Stoppart", and the list
  column carries the same word.
* The playback "Stopp" button is greyed out while nothing plays, so only
  one "Stopp" is ever active.

## 2.5 — 2026-11-10

* The two input fields ("Fade am Ende", "Pegel") were invisible in dark
  mode — only a gap between the labels. They now have a white background
  and a border, like the list.

## 2.4 — 2026-11-10

* 0 dBFS limit: any boost is capped so the peak stops at 0 dBFS instead
  of clipping. Checkbox "Anhebung auf 0 dB begrenzen" (on by default),
  `--ohne-begrenzung` to switch it off. The tool reports when it limited
  and how far.
* `print_pegel()` collects the level output in one place.

## 2.3 — 2026-11-10

* Level: new field "Pegel" (dB) plus checkbox "auf −1 dB Spitze
  normieren". Works on import and on existing patches
  (`--gain -3`, `--normieren`). The tool reports peak before/after and
  how many samples clipped.
* The selection stays marked after reloading the list — before, every
  change cleared it and the next click hit nothing.
* Backup folder names get `-2`, `-3`… if two changes happen within the
  same second; previously that aborted with "File exists".

## 2.2 — 2026-11-10

* "Zurücknehmen" is now called "Sicherung zurückholen" — it says where the
  state comes from, and the confirmation names the backup and its date.
* Restoring no longer picks the safety copy it just made: backups ending
  in `vor_zuruecknehmen` are skipped. Pressing the button twice therefore
  stays at the same state instead of jumping back and forth.

## 2.1 — 2026-11-10

* New button "Zurücknehmen": restores a patch from its most recent
  backup — recording and settings. This is how a fade is undone, since a
  fade changes the audio itself. Command line:
  `zuruecknehmen --patch N [--aus ORDNER]`.
  The state before restoring is saved again, so even this step can be
  reversed.
* Window 1080×690 — the middle row holds six buttons without clipping.

## 2.0 — 2026-11-10

* The fade can now be applied to an **existing** patch, not only on
  import: put a number into "Fade am Ende" and press "Einstellungen
  anwenden" (command line: `aendern --patch N --fade 2.5`).
  This changes the recording itself, so the whole patch folder is copied
  to `~/JamMan-Sicherung` first. Applying it twice fades twice.

## 1.9 — 2026-11-10

* Third stop mode added: "Finish" is stored as `StopAtEndOfLoop` in
  `patch.xml`. Found on a backup disk (patch 16 of an older card image),
  so the value is real and not invented. The dropdown now offers
  `fade`, `instant` and `finish`; the list shows "ende".

## 1.8 — 2026-11-10

* Fade length: the pedal has no setting for it — the manual (JamMan Solo
  XT, "Stop Modes") says Fade performs a fixed **10 second** fade out,
  and the card format has no field for a duration. So the length is now
  baked into the audio on import: field "Fade am Ende" in the window, or
  `aufspielen --fade 2.5` on the command line. The last N seconds are
  faded out linearly.

## 1.7 — 2026-11-10

* New button "Platz ändern": moves a patch to a given slot number, e.g.
  `Patch200` → `Patch28`. The target slot must be free; if it is taken,
  the error message points to "Tauschen".
  Command line: `platzieren --patch 200 --auf 28`.
* Five buttons in the middle row; all labels still fit.

## 1.6 — 2026-11-10

* New button "Umbenennen": renames the selected patch. The name lives in
  `patch.xml` (`<PatchName>`), the folder name (the slot) stays as it is.
  Command line: `aendern --patch N --name "..."`.
* Changing a name or a setting now backs up only `patch.xml` and
  `phrase.xml` instead of the whole patch folder — a copy of up to 90 MB
  per rename was wasteful and slow.
* Three button rows, so every label fits; window 1024×680.

## 1.5 — 2026-11-10

* New: swap two patches. Mark two rows (click the first, ⌘-click the
  second) and press "Tauschen" — the two change places, everything else
  stays untouched. Command line: `tauschen --patch N --mit M`.
  This differs from dragging, which shifts everything in between.
* A backup folder that cannot be written now produces a readable
  message instead of an exception inside the window.

## 1.4 — 2026-11-10

* Patches can be reordered by dragging them in the list. Dragging only
  renames folders; nothing is copied. A small text file with the old
  order (folder, name, UUID) is written to `~/JamMan-Sicherung` first, so
  any reordering can be undone by hand. The command line equivalent is
  `verschieben --patch N --nach M --keine-kopie`.

## 1.3 — 2026-11-10

* The list now shows stop mode, loop/single and rhythm for every patch,
  so it is visible whether a setting arrived.
* New button "Einstellungen anwenden": applies the three dropdowns to the
  **selected** patch. Before, the dropdowns only affected files being
  imported — changing them and then looking at an existing patch did
  nothing, which looked like the settings were ignored.
* Column header above the list (it used to appear below it), wider
  window, button rows redistributed so no label gets clipped.

## 1.2 — 2026-11-10

* Window: text was unreadable in macOS dark appearance (dark letters on a
  dark list — the patches were there, they just could not be seen).
  The list is now always white with black text, so it stays readable even
  if the system forces its own colours. A `selbsttest` command reports
  what the window should be showing.
* Window: requires Tk 8.6 or newer. Apple's Tk 8.5 (bundled with the
  Xcode Python 3.9) draws no text at all on macOS 26/27 — the window
  stays blank while the buttons appear. With only Tk 8.5 available the
  tool now says so and starts the text menu instead. A blank window
  passes an "can it open a window" test, which is why this went
  unnoticed before.
* Window: buttons no longer get cut off. Two rows, and the window has a
  minimum size, so "Stopp" is always visible.
* Window: the title shows the number of patches, and an empty list now
  says why it is empty.
* Card detection: if several drives contain a `JamManSoloXT` folder
  (for example an old backup disk), the most likely one wins — rated by
  name, removable media and number of patches. Earlier the volume that
  came first alphabetically won, which could be a backup.
* Card detection: the `JamManSoloXT` folder is also found when the case
  differs.
* `selbsttest` no longer requires tkinter — it used to fail on exactly
  those machines where it was needed (Homebrew Python 3.15 without
  `_tkinter`). It now reports system, Python, Tk, every known Python
  with its Tk status, the drives found and the first list line.

## 1.1 — 2026-10-26

* Linux support: card detection (`/media`, `/run/media`), playback via
  `aplay`/`paplay`/`mpv`/`ffplay`, unmount via `udisksctl`/`umount`,
  launcher `JamMan.sh`.
* Windows support: drive-letter detection, playback via `winsound`,
  eject via PowerShell/`mountvol`, launcher `JamMan.bat`.
* WAV conversion without ffmpeg: `afconvert` on macOS, ffmpeg if
  present, otherwise a minimal built-in conversion.

## 1.0 — 2026-10-26

* First release: list, play, import, change, move, delete, back up,
  clean, eject, GUI, text menu.
* Dry run by default; automatic backup before every write.
* Patch slot numbers are taken from the existing folder names — the tool
  never invents a numbering scheme (this was a bug in earlier builds:
  an existing `Patch200` caused every folder to be renamed to three
  digits, and the pedal then found no patches at all).
