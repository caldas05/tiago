"""Rhythm-scaled echo for any MIDI file.

Reads a MIDI file, takes the first part, appends a rhythm-scaled copy of
the theme starting at a chosen moment, and writes back tick-accurate MIDI
plus a visualization.

Usage:
    python polytime.py INPUT.mid [--at WHEN] [--scale RATIO] [-o OUT.mid]

WHEN accepts:
  - a beat count                : `8`, `17/2`
  - a bar count (`b` suffix)    : `2b` = 2 bars in the chosen time signature
  - absolute seconds (`s`)      : `3.5s` (converted using --bpm, default 120)

RATIO is any Fraction: `3/2` = 1.5× slower, `2` = twice as slow,
`2/3` = 1.5× faster.

Examples:
    python polytime.py frere.mid                       # echo at beat 16, 3/2 slower
    python polytime.py song.mid --at 2b --scale 2      # echo after 2 bars, twice as slow
    python polytime.py song.mid --at 3.5s --bpm 90     # echo at 3.5 seconds @ 90 bpm
    python polytime.py song.mid --time-sig 6/8 --at 1b # works with any time signature
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import mido

from score_io.parsers.midi import parse
from score_io.live.midi_file import save_mido, load_mido
from model.measure import TimeSignature, Measure
from model.duration import Duration
from model.events import Rest
from model.voice import Voice
from model.part import Part
from model.score import Score
from transforms.temporal import echo, scale_rhythm, shift_offset


def detect_bpm(path: str | Path) -> float | None:
    """Read the first set_tempo meta-event and return its BPM. None if absent."""
    for track in mido.MidiFile(str(path)).tracks:
        for msg in track:
            if msg.type == "set_tempo":
                return 60_000_000.0 / msg.tempo
    return None


def parse_scale(s: str, base_bpm: float = 120.0) -> Fraction:
    """Parse a rhythm scale. Accepts:
      - exact fractions:  '3/2', '17/8'
      - decimals:         '1.5', '0.6667'
      - math expressions: 'sqrt(2)', 'pi/3', '2**(1/3)'
      - BPM targets:      '60bpm' (scale = base_bpm / target — slower bpm → bigger scale)
    Math values are converted to Fractions via limit_denominator(10000).
    """
    import math
    s = s.strip()
    low = s.lower()
    if low.endswith("bpm"):
        target = _eval_math(s[:-3].strip())
        if target <= 0:
            raise ValueError(f"bpm must be positive: {s}")
        return Fraction(base_bpm / target).limit_denominator(10000)
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return Fraction(_eval_math(s)).limit_denominator(10000)


def _eval_math(s: str) -> float:
    """Restricted numeric eval: arithmetic + math.{sqrt,pi,e,log,exp,sin,cos,tan,pow}."""
    import math
    allowed = {
        "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
        "log": math.log, "exp": math.exp, "pow": pow,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "abs": abs,
    }
    code = compile(s, "<scale>", "eval")
    for name in code.co_names:
        if name not in allowed:
            raise ValueError(f"name {name!r} not allowed in scale expression")
    return float(eval(code, {"__builtins__": {}}, allowed))


def detect_time_signature(path: str | Path) -> TimeSignature | None:
    """Read the first time_signature meta-event from a MIDI file. Returns
    None if no signature is present (uncommon — most DAWs always write one).
    Tempo/key meta-events are ignored."""
    for track in mido.MidiFile(str(path)).tracks:
        for msg in track:
            if msg.type == "time_signature":
                return TimeSignature(msg.numerator, msg.denominator)
    return None


def parse_range(s: str, beats_per_bar: Fraction) -> tuple[Fraction, Fraction] | None:
    """Parse a 'start..end' range; each side accepts the same suffixes as
    _parse_when ('b' bars, 's' seconds, plain = beats). Empty/None → None.
    """
    if s is None:
        return None
    s = s.strip()
    if not s or ".." not in s:
        return None
    a, b = s.split("..", 1)
    start = _parse_when(a, beats_per_bar) if a.strip() else Fraction(0)
    end = _parse_when(b, beats_per_bar)
    if end <= start:
        raise ValueError(f"range end must exceed start: {s!r}")
    return start, end


def _parse_when(s: str, beats_per_bar: Fraction, bpm: float = 120.0) -> Fraction:
    s = s.strip().lower()
    if s.endswith("b"):
        return Fraction(s[:-1]) * beats_per_bar
    if s.endswith("s"):
        # Seconds → beats via bpm. A quarter note (one beat in this model)
        # lasts 60/bpm seconds.
        return Fraction(float(s[:-1]) * bpm / 60.0).limit_denominator(96)
    return Fraction(s)


def _flatten(part: Part) -> Voice:
    """Flatten one Part: concatenate every voice in every measure into a
    single voice with absolute offsets. All notes from every voice are kept.
    """
    out: list = []
    cum = Fraction(0)
    for m in part.measures:
        for v in m.voices:
            for ev in v.events:
                out.append(replace(ev, offset=cum + ev.offset))
        cum += m.time_signature.beats_per_measure
    return Voice(id="flat", events=tuple(sorted(out, key=lambda e: e.offset)))


def _flatten_score(score: Score) -> Voice:
    """Merge every Note from every Part/voice in `score` into one flat Voice.
    Rests are dropped (they'd collide across parts and add no MIDI info)."""
    from model.events import Note, Chord
    merged: list = []
    for p in score.parts:
        v = _flatten(p)
        merged.extend(e for e in v.events if isinstance(e, (Note, Chord)))
    return Voice(id="theme", events=tuple(sorted(merged, key=lambda e: e.offset)))


def _voice_to_part(voice: Voice, cap: Fraction, ts: TimeSignature, name: str) -> Part:
    """Wrap a flat voice into a Part with measures of capacity `cap`."""
    measures = _bin_into_measures(voice, cap, ts)
    # Preserve the voice id so save_mido names the track after it.
    new_measures = tuple(
        replace(m, voices=tuple(replace(v, id=voice.id) for v in m.voices))
        for m in measures
    )
    return Part(name=name, instrument=None, clef="treble", measures=new_measures)


def _bin_into_measures(voice: Voice, cap: Fraction, ts: TimeSignature) -> tuple[Measure, ...]:
    total = max((e.offset + e.duration.actual_beats for e in voice.events),
                default=Fraction(0))
    n_bars = max(1, int(-(-total // cap)))
    bins = [[] for _ in range(n_bars)]
    for e in voice.events:
        idx = int(e.offset // cap)
        if 0 <= idx < n_bars:
            bins[idx].append(replace(e, offset=e.offset - idx * cap))
    measures = []
    for i, bin_ in enumerate(bins):
        evs = tuple(bin_) if bin_ else (
            Rest(duration=Duration(value=cap), offset=Fraction(0)),
        )
        measures.append(Measure(
            number=i + 1, time_signature=ts,
            voices=(Voice(id="theme", events=evs),),
        ))
    return tuple(measures)


def polytime(
    input_path: str | Path,
    *,
    at: Fraction | str = Fraction(16),
    scale: Fraction | str = Fraction(3, 2),
    out: str | Path | None = None,
    diff_png: str | Path | None = None,
    time_signature: TimeSignature = TimeSignature(4, 4),
    scales: tuple[Fraction, ...] | None = None,
    ats: tuple[Fraction, ...] | None = None,
    combine: bool = True,
    viz_connectors: bool = True,
    theme_range: tuple[Fraction, Fraction] | None = None,
    theme_ranges: tuple[tuple[Fraction, Fraction] | None, ...] | None = None,
    output_range: tuple[Fraction, Fraction] | None = None,
) -> tuple[Path, Path]:
    """Append rhythm-scaled echoes to the first part of `input_path` and
    write a MIDI file. Returns (midi_path, viz_path).

    `scales`: per-voice rhythm scales (one entry per echo voice).
    `ats`: per-voice entry times. If None, voice k enters at `k*at` (the
    classic staggered pile-up). If provided, must match `scales` in length.
    `combine`: if True, the output MIDI contains the original theme + all
    echoes; if False, only the echoes.
    """
    input_path = Path(input_path)
    cap = time_signature.beats_per_measure
    at_f = at if isinstance(at, Fraction) else _parse_when(at, cap)
    if scales is None:
        scales_f = (scale if isinstance(scale, Fraction) else Fraction(scale),)
    else:
        scales_f = tuple(s if isinstance(s, Fraction) else Fraction(s) for s in scales)
    if ats is None:
        ats_f = tuple(at_f * k for k in range(1, len(scales_f) + 1))
    else:
        ats_f = tuple(
            a if isinstance(a, Fraction) else _parse_when(a, cap) for a in ats
        )
        if len(ats_f) != len(scales_f):
            raise ValueError(
                f"ats length {len(ats_f)} must match scales length {len(scales_f)}"
            )

    # load_mido reads tick-accurate from raw MIDI and gives one Voice per
    # track; the music21 path would drop chords and extra voices. We then
    # merge every track's notes into a single flat theme, preserving the
    # full polyphony of the input.
    score = load_mido(str(input_path), time_signature=time_signature)
    full_theme = _flatten_score(score)
    # theme_range / theme_ranges control *what gets echoed*, not what goes into
    # the output. When combine=True the full original is preserved as the theme
    # track no matter how narrow the echoed slice is.
    # theme_ranges (per-voice) takes precedence over theme_range (single).
    if theme_ranges is not None:
        if len(theme_ranges) != len(scales_f):
            raise ValueError(
                f"theme_ranges length {len(theme_ranges)} must match "
                f"scales length {len(scales_f)}"
            )
        per_voice_sources: list[Voice] = []
        for i, rng in enumerate(theme_ranges):
            if rng is None:
                per_voice_sources.append(full_theme)
                continue
            ts_start, ts_end = rng
            src = Voice(id="theme", events=tuple(
                replace(e, offset=e.offset - ts_start)
                for e in full_theme.events
                if ts_start <= e.offset < ts_end
            ))
            if not src.events:
                raise ValueError(
                    f"theme range {ts_start}..{ts_end} (voice {i+1}) "
                    f"contains no notes"
                )
            per_voice_sources.append(src)
    elif theme_range is not None:
        ts_start, ts_end = theme_range
        echo_source = Voice(id="theme", events=tuple(
            replace(e, offset=e.offset - ts_start)
            for e in full_theme.events
            if ts_start <= e.offset < ts_end
        ))
        if not echo_source.events:
            raise ValueError(f"theme range {ts_start}..{ts_end} contains no notes")
        per_voice_sources = [echo_source] * len(scales_f)
    else:
        per_voice_sources = [full_theme] * len(scales_f)

    def _fmt(x: Fraction) -> str:
        # Compact label: keep small fractions exact (3/2, 5/4) but render
        # irrational-derived ones (e.g. sqrt(2) ≈ 11482/8119) as a short decimal.
        if x.denominator <= 16:
            return str(x)
        return f"{float(x):.4g}"

    echo_voices: list[Voice] = []
    for k, (s, a, src) in enumerate(
        zip(scales_f, ats_f, per_voice_sources), start=1
    ):
        scaled = scale_rhythm(src, s)
        shifted = shift_offset(scaled, a)
        echo_voices.append(Voice(
            id=f"echo_{k}_x{_fmt(s)}@{_fmt(a)}", events=shifted.events,
        ))

    def _crop(v: Voice) -> Voice:
        if output_range is None:
            return v
        lo, hi = output_range
        kept = tuple(
            replace(e, offset=e.offset - lo)
            for e in v.events
            if lo <= e.offset < hi
        )
        return Voice(id=v.id, events=kept)

    # The exported "theme" track is the FULL original — independent of
    # theme_range, which only governs what gets echoed.
    theme_for_output = Voice(id="theme", events=full_theme.events)
    theme_for_output = _crop(theme_for_output)
    echo_voices = [_crop(v) for v in echo_voices]
    echo_voices = [v for v in echo_voices if v.events]

    # Each MIDI-bound voice becomes its own Part → its own MIDI track,
    # so DAWs can solo/mute them independently.
    exported: list[Voice] = (
        ([theme_for_output] if combine and theme_for_output.events else [])
        + echo_voices
    )
    if not exported:
        raise ValueError("output range eliminated every voice — widen the crop")
    parts = tuple(
        _voice_to_part(v, cap, time_signature, name=v.id) for v in exported
    )
    scales_label = ", ".join(_fmt(s) for s in scales_f)
    ats_label = ", ".join(_fmt(a) for a in ats_f)
    poly = Score(
        title=f"{input_path.stem} + echoes scales=[{scales_label}] ats=[{ats_label}]",
        parts=parts, metadata={},
    )

    out = Path(out) if out else input_path.with_name(f"{input_path.stem}_polytime.mid")
    save_mido(poly, str(out))

    viz_path = Path(diff_png) if diff_png else out.with_suffix(".svg")
    title = f"{input_path.stem} echoes scales=[{scales_label}] ats=[{ats_label}]"
    rows: list[tuple[str, Voice]] = []
    if combine and theme_for_output.events:
        rows.append(("theme", theme_for_output))
    for v in echo_voices:
        rows.append((v.id, v))
    ext = viz_path.suffix.lower()
    if ext == ".html":
        from viz.interactive import multi_row_html
        multi_row_html(rows, viz_path, title=title, combined=True)
    else:
        # Legacy SVG/PNG path: fall back to the old diff for CLI users.
        import matplotlib
        matplotlib.use("Agg")
        from viz import diff, trace
        all_echo_events = [e for v in echo_voices for e in v.events]
        echo_only = Voice(id="echo", events=tuple(
            sorted(all_echo_events, key=lambda e: e.offset)
        ))
        diff(trace(full_theme), echo_only, title=title,
             connectors=viz_connectors).savefig(viz_path)

    return out, viz_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="source MIDI file")
    ap.add_argument("--at", default="16",
                    help="when the echo enters: beats (e.g. 8, 17/2) or bars (2b)")
    ap.add_argument("--scale", default="3/2",
                    help="rhythm scale (3/2 = 1.5× slower, 2 = 2× slower, 2/3 = faster)")
    ap.add_argument("-o", "--out", default=None,
                    help="output MIDI path (default: <input>_polytime.mid)")
    ap.add_argument("--viz", choices=["svg", "png", "html"], default="svg",
                    help="visualization format: svg (default, vector), "
                         "png (raster), html (interactive pan/zoom in browser)")
    ap.add_argument("--time-sig", default=None,
                    help="override the file's time signature (e.g. 3/4); "
                         "default: read from the MIDI file's meta-event, "
                         "fall back to 4/4 if absent")
    ap.add_argument("--bpm", type=float, default=120.0,
                    help="tempo, only used to convert seconds in --at (default 120)")
    args = ap.parse_args()

    if args.time_sig is not None:
        num, den = (int(x) for x in args.time_sig.split("/"))
        ts = TimeSignature(num, den)
        source = "override"
    else:
        detected = detect_time_signature(args.input)
        ts = detected or TimeSignature(4, 4)
        source = "from file" if detected else "default (no meta-event found)"
    print(f"time signature: {ts.numerator}/{ts.denominator} ({source})")

    out, viz = polytime(
        args.input,
        at=_parse_when(args.at, ts.beats_per_measure, args.bpm),
        scale=Fraction(args.scale),
        out=args.out,
        diff_png=Path(args.out).with_suffix(f".{args.viz}") if args.out
                 else Path(args.input).with_name(
                     Path(args.input).stem + "_polytime." + args.viz),
        time_signature=ts,
    )
    print(f"Wrote {out} and {viz}")


if __name__ == "__main__":
    main()
