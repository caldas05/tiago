"""Bounded, perceptually-aware replacements for raw lcm-based alignment math.

Every alignment / period calculation in the analysis pass goes through this
module. Raw `math.lcm` on ratio denominators is mathematically correct but
musically useless past the point where (a) the listener stops grouping the
return as "the same canon coming back" and (b) soft-synths accumulate enough
tick/buffer drift to smear the coincidence anyway. So we cap by piece length
and by a perceptual horizon, and for everything beyond that we report
*near-alignments* — the moments where two voices come within epsilon of a
shared onset — which is what the ear actually tracks.
"""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from math import lcm
from typing import Iterable


# Default ~32 bars in 4/4 = 128 beats; about 30s at 120bpm. Configurable per
# call so the UI can expose it later.
PERCEPTUAL_HORIZON_BEATS_DEFAULT: Fraction = Fraction(128)

# Two onsets within this many beats are considered "almost coincident" for
# the purposes of structural-return detection. 1/8 beat ~= 31ms at 120bpm,
# well inside the ear's grouping window.
NEAR_ALIGN_EPSILON_BEATS_DEFAULT: Fraction = Fraction(1, 8)

# Cap applied to scale-ratio denominators before computing periods. Keeps
# `sqrt(2)`'s `Fraction.limit_denominator(10000)` representation from
# generating a five-digit lcm that no one will hear.
DENOMINATOR_CAP_DEFAULT: int = 64


@dataclass(frozen=True)
class NearAlignment:
    """One moment where two voices come within epsilon of sharing an onset."""
    beat: Fraction
    distance: Fraction  # absolute offset gap; 0 means exact coincidence
    voice_i_beat: Fraction
    voice_j_beat: Fraction


def _capped(scale: Fraction, denom_cap: int) -> Fraction:
    """Reduce a scale ratio to at most `denom_cap` in the denominator.
    Irrational-derived ratios collapse to a nearby rational the period math
    can actually use; small exact ratios pass through untouched."""
    return scale.limit_denominator(denom_cap)


def bounded_realign_period(
    scale_i: Fraction,
    scale_j: Fraction,
    *,
    total_beats: Fraction,
    horizon_beats: Fraction = PERCEPTUAL_HORIZON_BEATS_DEFAULT,
    denom_cap: int = DENOMINATOR_CAP_DEFAULT,
) -> Fraction | None:
    """Return the realignment period of two voices scaled by `scale_i` and
    `scale_j` against a shared theme, or None if it exceeds the smaller of
    `total_beats` and `horizon_beats`.

    Two voices play the same theme at scales s_i, s_j. After one full theme
    pass, voice i has consumed `T * s_i` beats of output and voice j has
    consumed `T * s_j` (T = theme length in beats). They realign on the
    *output* timeline when both have completed an integer number of theme
    passes — i.e. when output time is a common multiple of (T*s_i) and
    (T*s_j). The theme length T factors out of the *ratio* of those, so we
    work in scale-space directly: realignment period in beats per theme-unit
    is lcm(num_i*den_j, num_j*den_i) / (den_i * den_j).
    """
    si = _capped(scale_i, denom_cap)
    sj = _capped(scale_j, denom_cap)
    num = lcm(si.numerator * sj.denominator, sj.numerator * si.denominator)
    den = si.denominator * sj.denominator
    period = Fraction(num, den)
    cap = min(total_beats, horizon_beats)
    if period > cap:
        return None
    return period


def near_alignments(
    onsets_i: Iterable[Fraction],
    onsets_j: Iterable[Fraction],
    *,
    epsilon: Fraction = NEAR_ALIGN_EPSILON_BEATS_DEFAULT,
    max_hits: int = 32,
) -> list[NearAlignment]:
    """Find moments where an onset in voice i lies within `epsilon` beats of
    an onset in voice j. Returns up to `max_hits` hits, ranked by closeness
    (exact coincidences first).

    Both inputs must be sorted ascending. O(n+m) two-pointer scan.
    """
    a = sorted(onsets_i)
    b = sorted(onsets_j)
    hits: list[NearAlignment] = []
    i = j = 0
    while i < len(a) and j < len(b):
        ai, bj = a[i], b[j]
        d = ai - bj
        if d < 0:
            d = -d
        if d <= epsilon:
            hits.append(NearAlignment(
                beat=min(ai, bj), distance=d,
                voice_i_beat=ai, voice_j_beat=bj,
            ))
        if ai <= bj:
            i += 1
        else:
            j += 1
    hits.sort(key=lambda h: (h.distance, h.beat))
    return hits[:max_hits]


def effective_period_label(
    scale_i: Fraction,
    scale_j: Fraction,
    *,
    total_beats: Fraction,
    horizon_beats: Fraction = PERCEPTUAL_HORIZON_BEATS_DEFAULT,
    denom_cap: int = DENOMINATOR_CAP_DEFAULT,
) -> str:
    """Human-readable regime tag for a voice pair. Used by UI tooltips and
    the AnalysisReport's `regime` field."""
    period = bounded_realign_period(
        scale_i, scale_j,
        total_beats=total_beats,
        horizon_beats=horizon_beats,
        denom_cap=denom_cap,
    )
    if period is not None:
        return f"periodic@{period}"
    # Distinguish "would be periodic but past horizon" from "irrational, no
    # rational period at any cap" — the listener experience differs.
    raw = bounded_realign_period(
        scale_i, scale_j,
        total_beats=Fraction(10**9),
        horizon_beats=Fraction(10**9),
        denom_cap=denom_cap,
    )
    if raw is not None:
        return "above_horizon"
    return "non_periodic"
