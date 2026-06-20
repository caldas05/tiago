"""Vertical intervals between voice pairs.

For each pair (voice_i, voice_j) we walk the shared grid and, at every tick
where both voices have at least one sounding pitch, record every interval
class (0–11) between cross-voice pitch combinations. This gives a 12-bin
histogram per pair, plus a per-tick dissonance curve using configurable
weights.

Why pitch classes (mod 12) rather than absolute intervals: composers using
TIAGO care that "the echo sounds a fifth above" — the octave register is
incidental to the relationship. Octaves and unisons collapse to bin 0.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
from typing import Mapping

from analysis.grid import ScoreGrid, VoiceGrid


# Weights are 0 = stable, 1 = maximally dissonant. Octave/unison and the
# perfect consonances sit at the bottom; the tritone and minor 2nd/major 7th
# (same pc class) at the top. These are defaults — the UI can re-weight.
DEFAULT_DISSONANCE_WEIGHTS: dict[int, float] = {
    0: 0.0,   # unison/octave
    1: 1.0,   # m2 / M7
    2: 0.6,   # M2 / m7
    3: 0.3,   # m3 / M6
    4: 0.2,   # M3 / m6
    5: 0.1,   # P4 / P5  (treated as consonant; bin 7 mirrors)
    6: 1.0,   # tritone
    7: 0.1,   # P5 / P4
    8: 0.2,   # m6 / M3
    9: 0.3,   # M6 / m3
    10: 0.6,  # m7 / M2
    11: 1.0,  # M7 / m2
}


@dataclass(frozen=True)
class PairIntervals:
    """All vertical-interval data for one ordered voice pair (i, j)."""
    voice_i: str
    voice_j: str
    histogram: tuple[int, ...]  # length 12; count of (pitch_class_diff mod 12)
    dissonance_curve: tuple[float, ...]  # one value per tick; 0 when either voice silent
    overlap_ticks: int


def _pair_intervals(
    gi: VoiceGrid,
    gj: VoiceGrid,
    weights: Mapping[int, float],
) -> PairIntervals:
    n = min(len(gi.pitches), len(gj.pitches))
    histogram = [0] * 12
    curve = [0.0] * n
    overlap = 0
    for t in range(n):
        pi, pj = gi.pitches[t], gj.pitches[t]
        if not pi or not pj:
            continue
        overlap += 1
        tick_total = 0.0
        tick_count = 0
        for a in pi:
            for b in pj:
                ic = (a - b) % 12
                histogram[ic] += 1
                tick_total += weights.get(ic, 0.5)
                tick_count += 1
        # Mean dissonance over all cross-voice pairs at this tick — keeps the
        # curve comparable between dense and sparse moments.
        curve[t] = tick_total / tick_count if tick_count else 0.0
    return PairIntervals(
        voice_i=gi.voice_id,
        voice_j=gj.voice_id,
        histogram=tuple(histogram),
        dissonance_curve=tuple(curve),
        overlap_ticks=overlap,
    )


def _intra_voice_intervals(
    g: VoiceGrid,
    weights: Mapping[int, float],
) -> PairIntervals:
    """Dissonance *within* a single voice — i.e. among the simultaneous
    pitches of a chord. Reported as a self-pair (voice_i == voice_j) so the
    single-MIDI-with-chords case still produces meaningful output. Unordered
    pairs only (each chord-internal interval counted once)."""
    n = len(g.pitches)
    histogram = [0] * 12
    curve = [0.0] * n
    overlap = 0
    for t in range(n):
        pitches = g.pitches[t]
        if len(pitches) < 2:
            continue
        overlap += 1
        tick_total = 0.0
        tick_count = 0
        for a, b in combinations(pitches, 2):
            ic = abs(a - b) % 12
            histogram[ic] += 1
            tick_total += weights.get(ic, 0.5)
            tick_count += 1
        curve[t] = tick_total / tick_count if tick_count else 0.0
    return PairIntervals(
        voice_i=g.voice_id,
        voice_j=g.voice_id,
        histogram=tuple(histogram),
        dissonance_curve=tuple(curve),
        overlap_ticks=overlap,
    )


def all_pair_intervals(
    score_grid: ScoreGrid,
    *,
    weights: Mapping[int, float] | None = None,
    include_intra_voice: bool = True,
) -> tuple[PairIntervals, ...]:
    """Compute PairIntervals for every (i, j).

    With `include_intra_voice=True` (default), each voice also gets a self-
    pair (i == j) measuring the dissonance among its own simultaneous
    pitches (chords). Self-pairs whose histogram is all zero — i.e. voices
    with no chord moments at all — are dropped so the UI doesn't list them.
    """
    w = weights or DEFAULT_DISSONANCE_WEIGHTS
    voices = score_grid.voices
    out: list[PairIntervals] = []
    if include_intra_voice:
        for v in voices:
            self_pair = _intra_voice_intervals(v, w)
            if sum(self_pair.histogram) > 0:
                out.append(self_pair)
    for i in range(len(voices)):
        for j in range(i + 1, len(voices)):
            out.append(_pair_intervals(voices[i], voices[j], w))
    return tuple(out)
