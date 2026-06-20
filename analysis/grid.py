"""Shared rational beat grid: turn a list of Voices into a per-tick array of
sounding MIDI pitches. Substrate for every other analysis metric.

We don't use numpy — the grid is small (a few thousand ticks at most for
typical TIAGO outputs, since ticks_per_beat is denominator-capped) and a
list-of-sets keeps the interval pass branchless and stdlib-only.
"""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from math import gcd
from typing import Iterable

from model.events import Note, Rest, Chord
from model.voice import Voice

from analysis.horizons import DENOMINATOR_CAP_DEFAULT


@dataclass(frozen=True)
class VoiceGrid:
    """A voice rendered onto a shared tick grid.

    `pitches[t]` is the set of MIDI numbers sounding at tick `t`. Empty set =
    silence. Tick `t` represents beat `Fraction(t, ticks_per_beat)`.
    """
    voice_id: str
    pitches: tuple[frozenset[int], ...]
    onsets: tuple[Fraction, ...]  # absolute beats; one entry per Note/Chord event


@dataclass(frozen=True)
class ScoreGrid:
    """A bundle of voice grids sharing one tick resolution."""
    ticks_per_beat: int
    total_beats: Fraction
    voices: tuple[VoiceGrid, ...]
    snap_count: int  # number of events whose offset/duration didn't land on the grid


def _denom_lcm(values: Iterable[Fraction]) -> int:
    """LCM of denominators of an iterable of Fractions. Empty → 1. This is a
    *grid-resolution* lcm, not an alignment-period lcm, so it's fine to use
    raw lcm here — it's bounded by `cap` at the call site."""
    d = 1
    for v in values:
        d = d * v.denominator // gcd(d, v.denominator)
    return d


def choose_ticks_per_beat(
    voices: Iterable[Voice],
    *,
    cap: int = DENOMINATOR_CAP_DEFAULT,
) -> int:
    """Pick a tick resolution that captures every offset/duration exactly,
    capped at `cap` to keep the grid small. Past the cap, events are snapped
    to the nearest tick (and counted)."""
    denoms: list[Fraction] = []
    for v in voices:
        for e in v.events:
            denoms.append(e.offset)
            denoms.append(e.duration.actual_beats)
    natural = _denom_lcm(denoms) if denoms else 1
    return min(natural, cap)


def _event_pitches(ev) -> tuple[int, ...]:
    if isinstance(ev, Note):
        return (ev.pitch.midi,)
    if isinstance(ev, Chord):
        return tuple(p.midi for p in ev.pitches)
    return ()  # Rest


def _snap(beat: Fraction, ticks_per_beat: int) -> tuple[int, bool]:
    """Convert beats to ticks, returning (tick, snapped?)."""
    exact = beat * ticks_per_beat
    if exact.denominator == 1:
        return int(exact), False
    return round(float(exact)), True


def build_voice_grid(
    voice: Voice,
    *,
    total_beats: Fraction,
    ticks_per_beat: int,
) -> tuple[VoiceGrid, int]:
    """Render one voice onto the shared grid. Returns (grid, snap_count)."""
    n_ticks = int(total_beats * ticks_per_beat) + 1
    pitches: list[set[int]] = [set() for _ in range(n_ticks)]
    onsets: list[Fraction] = []
    snaps = 0
    for ev in voice.events:
        if isinstance(ev, Rest):
            continue
        start_tick, s1 = _snap(ev.offset, ticks_per_beat)
        end_beat = ev.offset + ev.duration.actual_beats
        end_tick, s2 = _snap(end_beat, ticks_per_beat)
        snaps += int(s1) + int(s2)
        onsets.append(ev.offset)
        for t in range(max(0, start_tick), min(n_ticks, end_tick)):
            for m in _event_pitches(ev):
                pitches[t].add(m)
    return (
        VoiceGrid(
            voice_id=voice.id,
            pitches=tuple(frozenset(s) for s in pitches),
            onsets=tuple(onsets),
        ),
        snaps,
    )


def build_score_grid(
    voices: list[Voice],
    *,
    cap: int = DENOMINATOR_CAP_DEFAULT,
) -> ScoreGrid:
    """Render every voice onto a shared grid sized to the longest voice."""
    if not voices:
        return ScoreGrid(ticks_per_beat=1, total_beats=Fraction(0), voices=(), snap_count=0)
    total = max(
        (
            max(
                (e.offset + e.duration.actual_beats for e in v.events),
                default=Fraction(0),
            )
            for v in voices
        ),
        default=Fraction(0),
    )
    tpb = choose_ticks_per_beat(voices, cap=cap)
    built = [build_voice_grid(v, total_beats=total, ticks_per_beat=tpb) for v in voices]
    total_snaps = sum(s for _, s in built)
    return ScoreGrid(
        ticks_per_beat=tpb,
        total_beats=total,
        voices=tuple(g for g, _ in built),
        snap_count=total_snaps,
    )
