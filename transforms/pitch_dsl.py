"""Tiny pitch-op DSL used by polytime to apply per-voice pitch transforms
to echoes. Stdlib-only, hand-rolled parser — small enough that pulling
lark/pyparsing would add more risk (PyInstaller bundling, arm64 wheels)
than value.

Grammar:
    op_chain   := op (';' op)*
    op         := identity | transpose | chromatic_invert | diatonic_invert
    identity   := '_' | ''         (no-op — preserves the voice)
    transpose  := 't' SIGN? INT   ('t+7', 't-5', 't7' all valid)
    chromatic_invert := 'i@' PITCH                 ('i@C4')
    diatonic_invert  := 'id@' PITCH '/' SCALE      ('id@E4/C-major')
    PITCH := letter [accidental] octave           ('C4', 'Bb3', 'F#5')
    SCALE := root '-' name                        ('C-major', 'Bb-natural_minor')

`apply_pitch_op(voice, "")` returns the voice unchanged. Invalid tokens
raise ValueError with the offending fragment so the UI can surface a
position-specific error.
"""
from __future__ import annotations
import re
from typing import Callable

from model.pitch import Pitch
from model.voice import Voice
from teoria.pitch import pitch_from_midi
from teoria.scale import (
    Scale, MAJOR, NATURAL_MINOR, DORIAN, PHRYGIAN, LYDIAN, MIXOLYDIAN,
    LOCRIAN, HARMONIC_MINOR, MELODIC_MINOR, CHROMATIC, WHOLE_TONE,
    PENTATONIC_MAJOR,
)
from transforms.melodic import (
    transpose_voice, invert_melody, invert_melody_diatonic,
)


_SCALE_BY_NAME: dict[str, tuple[int, ...]] = {
    "major": MAJOR,
    "natural_minor": NATURAL_MINOR,
    "minor": NATURAL_MINOR,
    "dorian": DORIAN,
    "phrygian": PHRYGIAN,
    "lydian": LYDIAN,
    "mixolydian": MIXOLYDIAN,
    "locrian": LOCRIAN,
    "harmonic_minor": HARMONIC_MINOR,
    "melodic_minor": MELODIC_MINOR,
    "chromatic": CHROMATIC,
    "whole_tone": WHOLE_TONE,
    "pentatonic_major": PENTATONIC_MAJOR,
}

# letter (+ optional # or b, but not both) + signed octave int
_PITCH_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d+)$")
_TRANSPOSE_RE = re.compile(r"^t([+-]?\d+)$")
_LETTER_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_pitch(token: str) -> Pitch:
    """Parse 'C4', 'Bb3', 'F#5', 'C-1' into a Pitch."""
    m = _PITCH_RE.match(token.strip())
    if not m:
        raise ValueError(f"bad pitch {token!r}; expected like 'C4', 'Bb3', 'F#5'")
    letter, acc, octave_s = m.groups()
    pc = _LETTER_TO_PC[letter.upper()]
    if acc == "#":
        pc += 1
    elif acc == "b":
        pc -= 1
    octave = int(octave_s)
    midi = (octave + 1) * 12 + (pc % 12)
    if not (0 <= midi <= 127):
        raise ValueError(f"pitch {token!r} out of MIDI range 0–127 (got {midi})")
    return pitch_from_midi(midi, spelling=letter.upper() + acc)


def parse_scale_token(token: str) -> Scale:
    """Parse 'C-major', 'Bb-natural_minor'."""
    if "-" not in token:
        raise ValueError(f"bad scale {token!r}; expected like 'C-major'")
    root_s, name = token.split("-", 1)
    name = name.strip().lower()
    if name not in _SCALE_BY_NAME:
        raise ValueError(
            f"unknown scale {name!r}; supported: {', '.join(sorted(_SCALE_BY_NAME))}"
        )
    # Root pitch is anchored at octave 4 for diatonic-step math; the octave
    # only affects intermediate calculations, not pc-class membership.
    root = parse_pitch(root_s.strip() + "4")
    return Scale(root=root, intervals=_SCALE_BY_NAME[name])


def parse_op(token: str) -> Callable[[Voice], Voice]:
    """Parse one op token into a Voice→Voice function."""
    t = token.strip()
    if t == "" or t == "_":
        return lambda v: v
    m = _TRANSPOSE_RE.match(t)
    if m:
        n = int(m.group(1))
        return lambda v: transpose_voice(v, n)
    if t.startswith("i@") and "/" not in t:
        axis = parse_pitch(t[2:])
        return lambda v: invert_melody(v, axis)
    if t.startswith("id@"):
        body = t[3:]
        if "/" not in body:
            raise ValueError(f"diatonic invert needs '/scale': {token!r}")
        axis_s, scale_s = body.split("/", 1)
        axis = parse_pitch(axis_s)
        scale = parse_scale_token(scale_s)
        return lambda v: invert_melody_diatonic(v, axis, scale)
    raise ValueError(
        f"unrecognised pitch op {token!r}; expected '_' | 't±N' | 'i@<pitch>' | 'id@<pitch>/<scale>'"
    )


def parse_op_chain(chain: str) -> Callable[[Voice], Voice]:
    """Parse 't+5;i@C4' into a single composed Voice→Voice. Empty → identity."""
    if chain is None:
        return lambda v: v
    parts = [p for p in chain.split(";")]
    if not parts:
        return lambda v: v
    fns = [parse_op(p) for p in parts]

    def applied(v: Voice) -> Voice:
        for fn in fns:
            v = fn(v)
        return v
    return applied


def apply_pitch_op(voice: Voice, chain: str | None) -> Voice:
    """One-shot convenience: parse `chain` and apply to `voice`."""
    return parse_op_chain(chain or "")(voice)
