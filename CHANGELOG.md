# Changelog

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
problem ran 3.102 on his JamMan Stereo under Linux and wrote back:
"Funktioniert, top." Which distribution he uses was not stated. The author
tested the tool itself on Ubuntu Studio and Windows 10. Writing to a Stereo
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
