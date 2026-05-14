# polytime

Drop a MIDI file, get a rhythm-scaled echo (or a stack of them) layered against the original. Runs entirely on your machine — the browser UI is just a local web page served by a tiny Python server.

## What it does

Given a theme, polytime adds one or more **echo voices**, each entering at its own time and playing at its own rhythmic scale. A scale of `3/2` means the echo plays 1.5× slower; `2` is twice as slow; `2/3` is 1.5× faster. Scales can also be decimals (`1.5`), math expressions (`sqrt(2)`, `2**(1/12)`, `pi/3`), or BPM targets (`60bpm` → "play this voice at 60 BPM regardless of source tempo").

Each echo voice is written to its **own named MIDI track**, so any DAW imports them as separable clips you can solo, mute, route, or reassign instruments to independently.

## Running it

### From source

```bash
pip install -r requirements.txt
python app.py
```

A browser tab opens at `http://127.0.0.1:<port>`. Drop a `.mid` file (or use the MIDI-keyboard recorder if you're in Chrome/Edge/Opera/Brave).

### As a standalone binary (no Python needed)

Grab the right zip from the [Releases](../../releases) page:

- **Windows:** `polytime-windows.zip` → `polytime.exe`. Double-click. First launch SmartScreen will warn "unrecognized app" — click *More info → Run anyway*.
- **macOS:** `polytime-macos.zip` → `polytime`. Apple Silicon (M1/M2/M3/M4). First launch Gatekeeper will block it; right-click → *Open* → confirm. (Or run `xattr -d com.apple.quarantine polytime` once.)
- **Linux:** `polytime-linux.zip` → `polytime`. `chmod +x polytime && ./polytime`.

### Building binaries yourself

- **Windows, local:** `pip install -r requirements-dev.txt`, then `.\build.bat` → `dist\polytime.exe`.
- **All three platforms, in the cloud:** push a version tag and GitHub Actions does it for you:
  ```bash
  git tag v0.1.0
  git push --tags
  ```
  A draft release with Windows/macOS/Linux artifacts appears under [Releases](../../releases) ~5–10 min later. See [`.github/workflows/release.yml`](.github/workflows/release.yml).

## The UI

- **Drop zone** — drag a `.mid` in, or click to choose, or use the MIDI keyboard recorder below.
- **MIDI keyboard input** — appears automatically if your browser supports the Web MIDI API. Plug a keyboard in, press Record, play, press Stop. Capture is free-time at millisecond precision; the BPM field controls how those milliseconds map to beats. The first key you press becomes beat 0.
- **at** — when each echo enters. One value (e.g. `2b`) staggers them: voice *k* enters at *k×at*. A comma list (`2b, 5b, 9b`) gives each voice its own entry time. Suffixes: `b` = bars, `s` = seconds, no suffix = beats.
- **scales** — comma-separated, one per echo voice. Examples below.
- **time sig** — optional override; otherwise the file's meta-event or 4/4 is used.
- **include original in MIDI** — if unchecked, the export contains only the echo tracks.
- **Generate** — runs everything; shows a before-viz on top, an after-viz below with one row per voice plus a combined-overlay row. Drag to pan, +/− to zoom, double-click to reset.
- **Download MIDI** — saves the multi-track output.

## Examples

| Field | Value | What it does |
|---|---|---|
| at | `2b` | echoes enter at bar 2, 4, 6, … |
| at | `2b, 5b, 9b` | per-voice entry times |
| scales | `3/2` | one echo, 1.5× slower |
| scales | `3/2, 2, 5/4` | three echoes |
| scales | `sqrt(2)` | irrational stretch — the echo never realigns with the theme |
| scales | `60bpm, 90bpm` | absolute tempo targets |
| scales | `2**(1/12)` | one-semitone temporal shift (equal-tempered) |

## Project layout

- `app.py` — local web server + single-page UI (drag-drop, recorder, viz embedding).
- `polytime.py` — the transform engine and CLI (`python polytime.py input.mid --at 2b --scale 3/2`).
- `model/`, `transforms/`, `score_io/`, `viz/` — the score model, transforms, MIDI I/O, and matplotlib-based piano-roll renderer.
- `tests/` — pytest suite. Run with `pytest tests/ -q`.

## Tech notes

- The MIDI reader is `score_io.live.midi_file.load_mido`, which goes through `mido` directly (tick-accurate) rather than music21 — so chords and multi-track inputs survive.
- The output writer is `save_mido`: one MIDI track per voice, named after the voice (`theme`, `echo_1_x3/2@8`, …).
- The browser-side MIDI-keyboard capture emits a standard Type-1 SMF in pure JavaScript and feeds it into the same upload path as a dropped file.

## License

Add one before you publish (MIT is the standard pick for small open-source utilities).
