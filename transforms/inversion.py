"""Constructive inversion of polytemporal coincidence structures.

The forward polyrhythm question is "given constant tempi, when do their pulses
coincide?" This module solves the inverse: the composer declares **where** the
lines must coincide — a set of beat times T = {T_1, ..., T_m} — and we return
a small collection of constant-tempo rhythmic lines whose union lands on every
prescribed point.

Method (subharmonic cover):

  1. Normalize. Map T onto a lowest-terms integer set K with rational scale S,
     so each target lands at beat K_j / S exactly (see `normalize`). The base
     tempo r is the grid those integers live on.
  2. Subharmonic cover. A subharmonic tempo r/a pulses on T_j iff a | n_j, so
     pick a set of factors covering every target; where pure subharmonics fall
     short, complete the cover with rational harmonics h·r/a.

Pure stdlib, Fraction-native — no model imports.
"""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm
from typing import Iterable


def _as_fraction(value) -> Fraction:
    """Coerce int / Fraction / numeric-string / float to an exact Fraction.

    Floats are snapped via limit_denominator so "0.4" entered in the UI
    becomes 2/5, not the binary-rounded 3602879701896397/9007199254740992."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value.strip())
    return Fraction(value).limit_denominator(1_000_000)


def normalize(times: Iterable) -> tuple[Fraction, list[int]]:
    """Step 1 of the method: map T onto a sorted integer set K.

    Returns (S, K) where each target lands at beat ``K_j / S`` exactly. K is
    reduced to lowest terms — its gcd is 1 — so the result depends only on the
    *ratios* of the targets, not their absolute scale. This makes the whole
    inversion **scale-invariant**: feeding T in beats vs the same T in bars
    (a uniform ×beats_per_bar) yields the same K and therefore the same cover
    structure (voice count, tempo ratios); only S changes to absorb the scale.
    Consequently S is rational, not integer (e.g. T in bars gives S = lcm/gcd).

    A 0 in the input is kept — that's how the composer tells the algorithm
    "voices must also pulse at beat 0" (so the first line's phase comes out 0
    and the voice starts at the downbeat); gcd(0, …) is the gcd of the rest,
    so the 0 survives reduction. Omit 0 and the first line's phase floats to
    min(K), i.e. voices enter mid-piece. Negatives are an error.
    """
    ts: list[Fraction] = []
    for t in times:
        f = _as_fraction(t)
        if f < 0:
            raise ValueError(f"coincidence times must be >= 0: {f}")
        ts.append(f)
    if not ts:
        raise ValueError("need at least one coincidence point")
    if not any(f > 0 for f in ts):
        raise ValueError("need at least one positive coincidence point — beat 0 alone is degenerate")
    D = 1
    for f in ts:
        D = lcm(D, f.denominator)
    K_raw = sorted({D * f.numerator // f.denominator for f in ts})
    g = gcd(*K_raw) or 1  # gcd(0) == 0; guard the (already-rejected) all-zero case
    S = Fraction(D, g)
    K = [k // g for k in K_raw]
    return S, K


@dataclass(frozen=True)
class Tempo:
    """A tempo ``h · r / a`` relative to the base tempo r (from `normalize`).

    Hits target T_j = n_j / r exactly when ``a`` divides ``h · n_j``. ``a=1``
    is the base tempo's harmonic family (every line hits every T_j — trivial);
    ``h=1`` is a pure subharmonic; general (h, a) is a harmonic-of-subharmonic.
    """

    h: int
    a: int

    def hits(self, n: int) -> bool:
        return (self.h * n) % self.a == 0

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.h, self.a)


def subharmonic_cover(
    times: Iterable, *, pair_with_base: bool = True
) -> tuple[Fraction, list[int], list[Tempo]]:
    """Constructive inversion à la paper §"subharmonics + h·r/a".

    Returns (S, K, tempi). With `pair_with_base=True` (default), the base
    tempo r is treated as an implicit extra voice, so each T_j needs only
    ONE selected tempo to be "paired". With False, each T_j must be hit by
    ≥ 2 selected tempi — when no n_j has two distinct subharmonic divisors,
    a harmonic-of-subharmonic ``h · r / a`` (h ≥ 2) is added.

    Subharmonic factors are greedy-set-cover-picked (largest new coverage
    first; ties broken by smaller a → "simpler" tempo).
    """
    S, K = normalize(times)
    K_pos = sorted({n for n in K if n > 0})
    if not K_pos:
        raise ValueError("subharmonic_cover needs at least one positive target")

    target = 1 if pair_with_base else 2
    coverage = {n: 0 for n in K_pos}
    chosen: list[int] = []
    # Candidate pool: every divisor > 1 of any n_j.
    candidates: set[int] = set()
    for n in K_pos:
        for a in range(2, n + 1):
            if n % a == 0:
                candidates.add(a)

    # Pass 1: pick subharmonics until every n_j is covered `target` times,
    # or no further subharmonic helps.
    while any(c < target for c in coverage.values()):
        best_a, best_score = None, 0
        for a in sorted(candidates - set(chosen)):
            score = sum(1 for n in K_pos
                        if coverage[n] < target and (n % a) == 0)
            if score > best_score:
                best_score, best_a = score, a
        if best_a is None:
            break
        chosen.append(best_a)
        for n in K_pos:
            if (n % best_a) == 0:
                coverage[n] += 1

    tempi: list[Tempo] = [Tempo(h=1, a=a) for a in sorted(chosen)]

    # Pass 2: any n_j still short needs a harmonic-of-subharmonic. Find a
    # subharmonic already in `chosen` that hits n, then add 2·r/a, 3·r/a, …
    # until coverage[n] reaches target.
    #
    # When NO proper subharmonic divides n (its only divisor is 1 → the
    # candidate pool never offered an a that hits it), fall back to integer
    # harmonics of the base, Tempo(h, 1) with h ≥ 2, which hit *every* n —
    # including n == 1, a target sitting exactly on the base grid unit (e.g.
    # K = [1, 2]). Without this fallback such an n is left under-covered: a
    # silent coincidence-with-no-voice failure.
    #
    # Dedup is by ratio (Fraction) not by (h, a) — otherwise h·r/a that
    # reduces to an existing tempo (e.g. Tempo(2,4) ≡ r/2) gets added as a
    # phantom voice. And ratio == 1 means h·r/a = r (the base tempo), which
    # would silently smuggle the base voice in when pair_with_base=False.
    existing_ratios = {Fraction(t.h, t.a) for t in tempi}
    H_MAX = 256  # safety cap; in practice h stays in single digits
    for n in list(K_pos):
        while coverage[n] < target:
            base_a = next((a for a in chosen if n % a == 0), None)
            new_t = None
            if base_a is not None:
                for h in range(2, H_MAX):
                    ratio = Fraction(h, base_a)
                    if ratio == 1 or ratio in existing_ratios:
                        continue
                    new_t = Tempo(h, base_a)
                    break
            if new_t is None:
                # No proper subharmonic pairs n: use a base harmonic h·r.
                for h in range(2, H_MAX):
                    if Fraction(h, 1) in existing_ratios:
                        continue
                    new_t = Tempo(h, 1)
                    break
            if new_t is None:
                break
            tempi.append(new_t)
            existing_ratios.add(new_t.ratio)
            for m in K_pos:
                if new_t.hits(m):
                    coverage[m] += 1
    return S, K_pos, tempi


def subharmonic_cover_report(
    times: Iterable, *, pair_with_base: bool = True
) -> dict:
    """JSON-ready summary of `subharmonic_cover`.

    Each tempo is reported with its (h, a), the ratio h/a relative to r, the
    period in beats (a / (h · S)), and which T_j it hits.
    """
    S, K, tempi = subharmonic_cover(times, pair_with_base=pair_with_base)
    items = []
    for t in tempi:
        period_beats = Fraction(t.a, t.h * S)
        items.append({
            "h": t.h,
            "a": t.a,
            "ratio_str": str(t.ratio),
            "period_beats": float(period_beats),
            "period_str": str(period_beats),
            "hits_K": [n for n in K if t.hits(n)],
            "hits_beats": [float(Fraction(n, S)) for n in K if t.hits(n)],
        })
    return {
        "scale": float(S),
        "scale_num": S.numerator,
        "scale_den": S.denominator,
        "K": K,
        "K_beats": [float(Fraction(n, S)) for n in K],
        "pair_with_base": pair_with_base,
        "tempi": items,
        "num_tempi": len(items),
    }
