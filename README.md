# TIAGO

**TI**me **AG**nostic **O**perator. Drop a MIDI file, get a rhythm-scaled echo (or a stack of them) layered against the original. Runs entirely on your machine — the browser UI is just a local web page served by a tiny Python server.

## What it does

Given a theme, TIAGO adds one or more **voices**, each entering at its own time, playing at its own rhythmic scale, and optionally transformed in pitch (transpose / chromatic inversion).

- A **scale** of `3/2` plays the voice 1.5× slower; `2` is twice as slow; `2/3` is 1.5× faster.
- Scales accept decimals (`1.5`), math (`sqrt(2)`, `2**(1/12)`, `pi/3`), or absolute BPM targets (`60bpm`).
- A **pitch** op transposes (`+7` semitones = up a fifth) or inverts chromatically around an axis (e.g. C4: G4 → F3).

Each voice is written to its **own named MIDI track**, so any DAW imports them as separable clips you can solo, mute, route, or reassign instruments to independently.

**🎯 Polytemporal alignment.** Beyond manually chosen scales, TIAGO ships a *constructive-inversion* generator: tell it where you want voices to coincide (`0b, 4b, 10b` or `0.4, 0.5, 0.7, 1.4`) and it computes the scales and entry points for you.

- **Subharmonic cover** — voices all start at beat 0, each pulsing at a subharmonic of a common base tempo (with rational harmonics `h·r/a` where pure subharmonics fall short). Every prescribed point is hit by ≥ 2 voices simultaneously. Dense, hocketed texture.

## Running it

### Standalone binary (no Python needed)

Grab the right asset from the [Releases](../../releases) page:

- **Windows** — `tiago-windows.zip` → `tiago.exe`. Double-click. First launch SmartScreen will warn "unrecognized app" — click *More info → Run anyway*.
- **macOS (Apple Silicon: M1/M2/M3/M4)** — `tiago-macos.dmg`. Double-click to mount, drag `tiago.app` into the `Applications` shortcut, eject. First launch: open `Applications`, **right-click** `tiago` → *Open* → click *Open* in the warning dialog. After that, double-click works. Intel Macs aren't supported — run from source instead.
- **Linux** — `tiago-linux.zip` → `tiago`. `chmod +x tiago && ./tiago`.

### From source

```bash
pip install -r requirements.txt
python app.py
```

A browser tab opens at `http://127.0.0.1:<port>`.

### Browser support

| Browser | File drop · voices · download | Internal-synth playback | External MIDI output | MIDI keyboard recording |
|---|---|---|---|---|
| Chrome / Edge / Brave / Opera | ✅ | ✅ | ✅ | ✅ |
| Safari | ✅ | ✅ | ❌ | ❌ |
| Firefox | ✅ | ✅ | ❌ | ❌ |

Safari and Firefox don't ship the Web MIDI API. Everything in TIAGO works in them **except** sending playback to an external MIDI device and recording from a hardware keyboard.

## The UI, top to bottom

### 1. Drop zone

Drop one or more `.mid` files or scores (`.musicxml` / `.xml` / `.mxl`), or click to choose. A dropped score is split into one input voice per part / staff / inner-voice — every line becomes its own row you can echo or leave alone. The drop zone shrinks to a thin strip after the first input lands.

### 2. MIDI keyboard recorder (Chromium browsers only)

Plug a keyboard in, click **● Record**, play, **■ Stop**. The recording is added to the input list at a provisional 120 BPM. To set the real tempo, click **▶ tap a bar** on that input's row, then drop **two clicks one bar apart** on the recording's row in the piano roll. TIAGO measures the span between them, computes the BPM, and re-stamps the MIDI. Re-tap any time to refine.

The **🔊 Monitor input** checkbox echoes your keystrokes through the playback output as you play, so you hear yourself.

### 3. Voices

Click **+ add voice** for a manually configured voice. Each row shows:

- a coloured numbered circle — **click to mute/unmute** this voice
- **source** — which input to echo
- **scale** — the rhythm multiplier (`3/2`, `sqrt(2)`, `60bpm`, …)
- **🔁** — per-voice loop. When on, the voice restarts as soon as its content ends. Multiple loopers at different scales generate a continuous polyrhythmic texture.
- **⋯** — expand for the rest of the controls:
  - **range** — which beat range of the source to use (empty = whole input). Click **pick** to draw on the piano roll.
  - **start** — when this voice enters: beats (`6`), bars (`2b`), or seconds (`2s`).
  - **pitch** — `none`, `transpose ±N semitones`, or `invert (chromatic)` around an axis.
- **×** — delete

### 4. Polytemporal alignment

Always-visible card under the voices once you have an input. Fill in:

- **source** — which input the generated voices echo
- **alignment points** — comma-separated beats where voices should coincide. Suffix `b` = bars (`0b, 4b, 10b`); plain numbers are beats (`0, 1/3, 2/3, 1`). Include `0` to anchor a voice on the downbeat; omit it and voices enter mid-piece.
- **base tempo counts** — treats the implicit base r as one of the voices, so each point only needs *one* selected tempo on it

Click **Generate**. The computed voices are added to the list above with their scales and starts filled in.

### 5. Live playback

Internal synth always available. On Chromium browsers you can additionally route to any system MIDI device. **▶ Play / ⏸ Pause / ⏹ Stop**, speed and volume sliders. Looping is **per-voice** (the 🔁 on each row) — there is no global loop. A looping voice keeps cycling while you change settings on the others.

### 6. Piano roll

One row per voice plus a *combined* overlay. **Drag** to pan, **scroll** to zoom toward the cursor. **Pick** buttons on voices put the roll into pick mode — click two points to define a range, or click once to set a start.

### 7. Footer

- **⬇ Download MIDI** — one MIDI track per input + one per voice, each named so a DAW imports them as separable clips.
- **⬇ Download score** — MusicXML. A panel lets you tick voices to include and choose:
  - *Download each separately* (a `.zip` of one `.musicxml` per voice) — each notated at its original rhythm with a tempo mark carrying its speed. Irrational ratios like `sqrt(2)` engrave cleanly because the ratio lives in the tempo (♩≈84.85), not in unnotatable rhythm.
  - *Merge* — all ticked voices on one multi-staff score aligned on a shared pulse and quantized to 16ths/triplets. Reads best when scales are compatible (rational, near each other).
- **extra options** — *time signature* override (cosmetic: barring only) and *output crop* (trim final MIDI to a beat range, e.g. `0..16`).
- **× Quit** — stops the server and closes TIAGO.

## Quick reference

| Field | Value | What it does |
|---|---|---|
| start | `2b` | voice enters at bar 2 |
| start | `1.5` | voice enters at beat 1.5 |
| scale | `3/2` | 1.5× slower |
| scale | `sqrt(2)` | irrational stretch — never realigns with the theme |
| scale | `60bpm` | absolute tempo target |
| scale | `2**(1/12)` | one-semitone temporal shift (equal-tempered) |
| pitch | `t+12` | up an octave |
| pitch | `i@C4` | invert chromatically around C4 |
| alignment points | `0b, 4b, 12b` | voices coincide at bars 1, 5, 13 |
| alignment points | `0, 1/3, 2/3, 1` | voices coincide at those beats |

## CLI (alternative to the UI)

```bash
python polytime.py input.mid --at 2b --scale 3/2 --pitch-op t+7
```

Flags: `--at`, `--scale`, `--out`, `--pitch-op`, `--time-signature`, `--bpm`. See `python polytime.py --help`.

## Building binaries yourself

- **Windows, local:** `pip install -r requirements-dev.txt`, then `.\build.bat` → `dist\tiago.exe`.
- **All three platforms, in CI:** push a version tag and GitHub Actions builds them:
  ```bash
  git tag v0.4.0
  git push --tags
  ```
  A draft release with Windows / macOS / Linux artifacts appears under [Releases](../../releases) ~5–10 min later. See [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Tech notes

- MIDI is read via `mido` directly (tick-accurate). Chords and multi-track inputs survive intact.
- Each output voice gets its own named MIDI track via `save_mido`.
- The MIDI keyboard recorder writes a Type-1 SMF in pure JavaScript and feeds it into the same upload path as a dropped file.
- The internal synth uses Web Audio (`AudioContext` / `webkitAudioContext`) — works in Safari and Firefox too.
- Constructive-inversion algorithms live in [`transforms/inversion.py`](transforms/inversion.py), covered by [`tests/test_inversion.py`](tests/test_inversion.py).

## License

Add one before you publish.
