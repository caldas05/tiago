# polytime

Drop a MIDI file, get a rhythm-scaled echo (or a stack of them) layered against the original. Runs entirely on your machine — the browser UI is just a local web page served by a tiny Python server.

## What it does

Given a theme, polytime adds one or more **echo voices**, each entering at its own time, playing at its own rhythmic scale, and optionally transformed in pitch (transpose / chromatic inversion).

- A **scale** of `3/2` plays the echo 1.5× slower; `2` is twice as slow; `2/3` is 1.5× faster.
- Scales accept decimals (`1.5`), math (`sqrt(2)`, `2**(1/12)`, `pi/3`), or absolute BPM targets (`60bpm` → "play this voice at 60 BPM regardless of source tempo").
- A **pitch** op transposes (`t+7` = up a fifth, `t-12` = down an octave) or inverts (`i@C4` = mirror around C4 chromatically).

Each echo voice is written to its **own named MIDI track**, so any DAW imports them as separable clips you can solo, mute, route, or reassign instruments to independently.

## Running it

### From source

```bash
pip install -r requirements.txt
python app.py
```

A browser tab opens at `http://127.0.0.1:<port>`. polytime tries to open Chrome / Edge / Brave / Chromium first (for MIDI keyboard support), falling back to your system default.

### As a standalone binary (no Python needed)

Grab the right asset from the [Releases](../../releases) page:

- **Windows:** `polytime-windows.zip` → `polytime.exe`. Double-click. First launch SmartScreen will warn "unrecognized app" — click *More info → Run anyway*.
- **macOS (Apple Silicon — M1/M2/M3/M4):** `polytime-macos.dmg`. Double-click to mount, drag `polytime.app` into the `Applications` shortcut, eject. First launch: open `Applications`, **right-click** `polytime` → *Open* → click *Open* in the warning dialog. (Mac blocks unsigned apps on a normal double-click; right-click → Open is the one-time override. After that, double-click works forever.) Intel Macs aren't supported — run from source instead.
- **Linux:** `polytime-linux.zip` → `polytime`. `chmod +x polytime && ./polytime`.

### Browser support

| Browser | File drop, polytime, download | Internal-synth playback | External MIDI device output | MIDI keyboard recording |
|---|---|---|---|---|
| Chrome / Edge / Brave / Opera | ✅ | ✅ | ✅ | ✅ |
| Safari | ✅ | ✅ | ❌ | ❌ |
| Firefox | ✅ | ✅ | ❌ | ❌ |

Safari and Firefox don't ship the Web MIDI API. Everything in polytime works in them **except** sending playback to an external MIDI device and recording from a hardware keyboard. The internal synth still plays things back through the tab.

### Building binaries yourself

- **Windows, local:** `pip install -r requirements-dev.txt`, then `.\build.bat` → `dist\polytime.exe`.
- **All three platforms, in CI:** push a version tag and GitHub Actions builds them for you:
  ```bash
  git tag v0.3.2
  git push --tags
  ```
  A draft release with Windows / macOS / Linux artifacts appears under [Releases](../../releases) ~5–10 min later. See [`.github/workflows/release.yml`](.github/workflows/release.yml).

## The UI

The page is intentionally minimal. Top to bottom:

### Inputs

- **Drop zone** — drop one or more `.mid` files, or click to choose. Multiple files are merged as parallel input voices (each gets its own row in the piano roll).
- **MIDI keyboard panel** (Chromium browsers only) — appears automatically when Web MIDI is supported. Plug a keyboard in, click **● Record**, play, click **■ Stop**. The recording is added as a new input.
  - **bpm** controls how millisecond timing maps to musical beats in the saved MIDI. *It does not change how your recording sounds* — the wall-clock rhythm is preserved exactly. It only affects where bar lines appear, what `at 2b` means in seconds, and how a DAW labels note durations. If you don't know your tempo, leave it at 120 and use `at 2s` (seconds) for echo entries.
  - **🔊 Monitor input** echoes your keystrokes to the playback output as you play, so you hear yourself.

### Echoes

Click **+ add polytime** to add an echo voice. Each echo has:

- **source** — which input to scale (defaults to the first input).
- **scale** — the rhythm multiplier (`3/2`, `2`, `sqrt(2)`, `60bpm`, etc.).
- **pitch** — optional pitch op:
  - `none` (default) — keep original pitches.
  - `transpose` — semitone shift (`+7` = up a fifth, `-12` = down an octave).
  - `invert (chromatic)` — mirror every pitch around an axis (e.g. axis = C4 maps G4 → F3).
- **range** — which beat range of the source to use (leave empty for the whole input). Click **pick** to draw the range directly on the piano roll.
- **at** — when this echo enters, in beats (no suffix), bars (`2b`), or seconds (`2s`).
- **include** — uncheck to mute this echo without removing it.

### Extra options

Collapsed by default — click **extra options** to reveal:

- **time signature** — overrides the file's meta-event (otherwise auto-detected, default 4/4). *Purely cosmetic*: it only changes where bar lines fall in the piano roll and the bar grouping in the exported MIDI. It does not change pitch or timing of any note. Useful when an echo's scale interacts musically with a non-4/4 meter, or when the file's tagged meter is wrong.
- **output crop (beats)** — trims the final MIDI to a beat range. Example: `0..16` exports only the first 16 beats. Leave empty to export everything.

### Live playback

Plays whatever's currently in the piano roll. The internal synth always works (uses Web Audio). On Chromium browsers you can additionally route to any system MIDI device (a GM synth, an IAC bus, etc.).

Standard transport controls: ▶ play, ⏸ pause, ⏹ stop, speed slider, volume, loop toggle.

### Piano roll

One canvas, one row per echo plus a **combined overlay** row at the bottom. Defaults to *combined only* — uncheck the box to expand into per-voice rows.

- **Drag** to pan, **scroll** to zoom toward the cursor.
- **Click** sets a selection start; clicking again sets the end. Drag for free-precision range. The selection auto-fills the echo's source `range` (when "pick" mode is active).
- Each row sizes its pitch axis to its own notes, so transposed/inverted echoes always fit inside their row.

### Download

After processing, **⬇ Download MIDI** appears above the piano roll. The export contains one MIDI track per input + one per echo, each named (`theme`, `echo_1_x3/2@8_t+7`, …) so a DAW shows them as separable, soloable clips.

## Examples

| Field | Value | What it does |
|---|---|---|
| at | `2b` | echoes enter at bar 2, 4, 6, … |
| at | `2b, 5b, 9b` | per-voice entry times |
| scale | `3/2` | 1.5× slower |
| scale | `sqrt(2)` | irrational stretch — never realigns with the theme |
| scale | `60bpm` | absolute tempo target |
| scale | `2**(1/12)` | one-semitone temporal shift (equal-tempered) |
| pitch | `transpose +12` | up an octave |
| pitch | `invert · axis C4` | mirror around C4: G4 → F3, A4 → D#3 |

## CLI (no UI)

```bash
python polytime.py input.mid --at 2b --scale 3/2 --pitch-op t+7
```

Flags: `--at`, `--scale`, `--out`, `--pitch-op`, `--time-signature`, `--bpm`. See `python polytime.py --help`.

## Project layout

- `app.py` — local web server + single-page UI (drag-drop, recorder, viz, live playback).
- `polytime.py` — the transform engine and CLI.
- `model/`, `transforms/`, `score_io/`, `teoria/` — score model, transforms, MIDI I/O, music theory primitives.
- `analysis/` — scaffolding for upcoming analytical features (grid, horizons, pair-intervals). Not wired into the current UI.
- `tests/` — pytest suite. Run with `pytest tests/ -q`.

## Tech notes

- MIDI is read via `mido` directly (tick-accurate) rather than music21 — chords and multi-track inputs survive.
- Output writer is `save_mido`: one MIDI track per voice, named after the voice.
- The MIDI keyboard recorder emits a standard Type-1 SMF in pure JavaScript and feeds it into the same upload path as a dropped file.
- The internal synth uses Web Audio (`AudioContext` / `webkitAudioContext`) — works in every modern browser including Safari and Firefox.

## License

Add one before you publish (MIT is the standard pick for small open-source utilities).
