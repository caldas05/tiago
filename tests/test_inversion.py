"""Tests for transforms.inversion — the subharmonic-cover inversion method.

The module is pure Fraction math, so tests are too. Two contracts under test:
(1) normalization reduces T to a lowest-terms integer set K (scale-invariant)
matching the worked example from the paper, and (2) the subharmonic cover hits
every prescribed point with the required number of voices.
"""
from __future__ import annotations
from fractions import Fraction

import pytest

from transforms.inversion import (
    Tempo,
    normalize,
    subharmonic_cover,
    subharmonic_cover_report,
)


class TestNormalize:
    def test_paper_example(self):
        # Section text: T = {0.4, 0.5, 0.7, 1.4} → K = {4, 5, 7, 14}, S = 10.
        S, K = normalize([Fraction(2, 5), Fraction(1, 2),
                          Fraction(7, 10), Fraction(7, 5)])
        assert S == 10
        assert K == [4, 5, 7, 14]

    def test_floats_snapped_via_limit_denominator(self):
        # 0.4 is binary-inexact; the float path must round to 2/5.
        S, K = normalize([0.4, 0.5, 0.7, 1.4])
        assert S == 10
        assert K == [4, 5, 7, 14]

    def test_string_inputs(self):
        S, K = normalize(["3/2", "5/2", "4"])
        assert S == 2
        assert K == [3, 5, 8]

    def test_integer_times(self):
        # K is reduced to lowest terms: {4,8,12,16} shares gcd 4, so the
        # canonical form is {1,2,3,4} with S = 1/4 (target = K_j / S).
        S, K = normalize([4, 8, 12, 16])
        assert S == Fraction(1, 4)
        assert K == [1, 2, 3, 4]

    def test_scale_invariance(self):
        # The reason K is reduced: a uniform scaling of the targets (e.g. the
        # same points expressed in bars = × beats_per_bar) must give the SAME
        # K and the same cover; only S absorbs the scale. Without this, bars
        # vs beats silently changed the voice count.
        S1, K1 = normalize([Fraction(2, 5), Fraction(1, 2),
                            Fraction(7, 10), Fraction(7, 5)])
        S2, K2 = normalize([Fraction(8, 5), Fraction(2),
                            Fraction(14, 5), Fraction(28, 5)])  # ×4 (bars)
        assert K1 == K2 == [4, 5, 7, 14]
        assert S1 == 10 and S2 == Fraction(5, 2)  # bars 4× longer → S 4× smaller

    def test_zero_kept_when_explicit(self):
        # 0 in the input = composer asking voices to also pulse at the
        # downbeat. gcd(0, …) is the gcd of the rest, so the 0 survives
        # reduction; the algorithm then picks phase 0 for the first line.
        # Omit 0 and the first line floats to min(K).
        S, K = normalize([0, 4, 8])
        assert K == [0, 1, 2]
        S2, K2 = normalize([4, 8])
        assert K2 == [1, 2]

    def test_duplicates_and_unsorted(self):
        S, K = normalize([Fraction(8), Fraction(4), Fraction(4), Fraction(12)])
        assert K == [1, 2, 3]  # {4,8,12} dedup → reduced by gcd 4

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            normalize([Fraction(-1), Fraction(4)])

    def test_empty_or_zero_only_rejected(self):
        with pytest.raises(ValueError):
            normalize([])
        with pytest.raises(ValueError):
            normalize([Fraction(0)])  # 0 alone is degenerate

    def test_zero_kept_and_reduced_with_positives(self):
        # "0b, 4b, 10b" from the UI (= 0,16,40 beats) reduces to the canonical
        # {0,2,5}: the explicit 0 survives reduction (so a voice can anchor on
        # the downbeat), and the result is identical to the already-reduced
        # ratios {0,2,5} — scale invariance.
        S, K = normalize([Fraction(0), Fraction(16), Fraction(40)])
        assert K == [0, 2, 5]
        _, K2 = normalize([Fraction(0), Fraction(2), Fraction(5)])
        assert K2 == K


class TestSubharmonicCover:
    """Paper §"subharmonics + h·r/a": each T_j must be hit by ≥ 1 selected
    tempo (when the base r counts) or ≥ 2 (when it doesn't)."""

    def test_paper_example_pair_with_base(self):
        # T = {0.4, 0.5, 0.7, 1.4}, n = {4, 5, 7, 14}, r = 10.
        # With base r counted, every n_j needs one subharmonic divisor.
        # Greedy: a=2 covers {4,14}, then a=5 covers {5}, then a=7 covers {7}.
        S, K, tempi = subharmonic_cover(
            [Fraction(2, 5), Fraction(1, 2), Fraction(7, 10), Fraction(7, 5)]
        )
        assert S == 10 and K == [4, 5, 7, 14]
        assert tempi == [Tempo(1, 2), Tempo(1, 5), Tempo(1, 7)]
        # Every n_j is hit by base (trivially) plus at least one of these.
        for n in K:
            assert any(t.hits(n) for t in tempi)

    def test_paper_example_no_base(self):
        # Without the base counted, each n needs ≥ 2 hits. n=5 and n=7 each
        # admit only one subharmonic (a=5 and a=7), so harmonics 2·r/5 and
        # 2·r/7 are forced.
        _, K, tempi = subharmonic_cover(
            [Fraction(2, 5), Fraction(1, 2), Fraction(7, 10), Fraction(7, 5)],
            pair_with_base=False,
        )
        # Per-n coverage ≥ 2.
        for n in K:
            hits = sum(1 for t in tempi if t.hits(n))
            assert hits >= 2, (n, hits, tempi)
        # The harmonics-of-subharmonics fall out: 2·r/5 (for n=5) and 2·r/7
        # (for n=7) must appear.
        assert Tempo(2, 5) in tempi
        assert Tempo(2, 7) in tempi

    def test_scale_invariant_bars_vs_beats(self):
        # The bug report: T = {0.4,0.5,0.7,1.4} gave 6 voices, but the same
        # points in bars (×beats_per_bar) gave 4 — because the bar scaling
        # injected a common factor that the divisor cover exploited. With K
        # reduced to lowest terms the structure is identical; only S differs.
        base = [Fraction(2, 5), Fraction(1, 2), Fraction(7, 10), Fraction(7, 5)]
        bars = [t * 4 for t in base]  # same points expressed in 4/4 bars
        S1, K1, t1 = subharmonic_cover(base, pair_with_base=False)
        S2, K2, t2 = subharmonic_cover(bars, pair_with_base=False)
        assert K1 == K2 == [4, 5, 7, 14]
        assert t1 == t2 and len(t1) == 6   # not 4
        assert S2 == S1 / 4                 # bars only rescale the grid

    def test_report_shape(self):
        rep = subharmonic_cover_report(
            [Fraction(2, 5), Fraction(1, 2), Fraction(7, 10), Fraction(7, 5)]
        )
        assert rep["scale"] == 10
        assert rep["scale_num"] == 10 and rep["scale_den"] == 1
        assert rep["K"] == [4, 5, 7, 14]
        assert rep["num_tempi"] == 3
        # ratios for the picked subharmonics are 1/2, 1/5, 1/7.
        ratios = {t["ratio_str"] for t in rep["tempi"]}
        assert ratios == {"1/2", "1/5", "1/7"}

    def test_no_base_smuggled_in_when_pair_with_base_false(self):
        # Pass-2 dedup must compare reduced ratios, not (h, a): with base_a = 2
        # the first harmonic h=2 gives h·r/a = r (ratio 1) — the base tempo,
        # which contradicts pair_with_base=False and must be skipped. ({2,4}
        # would reduce to {1,2}; {2,3} is already lowest-terms and still drives
        # base_a = 2 in pass 2.)
        _, K, tempi = subharmonic_cover(
            [Fraction(2), Fraction(3)], pair_with_base=False)
        assert K == [2, 3]
        ratios = [Fraction(t.h, t.a) for t in tempi]
        assert Fraction(1) not in ratios, (tempi, "base tempo smuggled in")
        # And every n must still be hit by ≥ 2 distinct (= different ratio) tempi.
        for n in K:
            hitting = {Fraction(t.h, t.a) for t in tempi if t.hits(n)}
            assert len(hitting) >= 2, (n, hitting, tempi)

    def test_no_duplicate_ratios_in_pass_two(self):
        # The dedup must compare reduced ratios — Tempo(2, 4) ≡ r/2, not a
        # new voice. Any K that forces a pass-2 harmonic on an even base_a
        # whose half is already in `chosen` exercises this.
        _, K, tempi = subharmonic_cover(
            [Fraction(n) for n in (4, 8, 12)], pair_with_base=False)
        ratios = [Fraction(t.h, t.a) for t in tempi]
        assert len(ratios) == len(set(ratios)), (tempi, "ratio duplicated")

    @pytest.mark.parametrize("times", [
        [Fraction(1, 2), Fraction(1)],   # K = [1, 2]: min(K) == 1
        [Fraction(1), Fraction(2)],      # K = [1, 2] via integers
        [Fraction(1), Fraction(3)],      # K = [1, 3]: 1 has no proper divisor
        [Fraction(2), Fraction(4), Fraction(6)],  # no 1, regression guard
    ])
    def test_every_n_double_covered_no_base(self, times):
        # pair_with_base=False promises every n hit by ≥ 2 distinct ratios.
        # When min(K) == 1 (a target on the base grid unit) no proper
        # subharmonic divides it, so the cover must fall back to base
        # harmonics h·r (h ≥ 2) rather than silently leaving it uncovered.
        _, K, tempi = subharmonic_cover(times, pair_with_base=False)
        for n in K:
            hitting = {Fraction(t.h, t.a) for t in tempi if t.hits(n)}
            assert len(hitting) >= 2, (n, hitting, tempi)

    def test_unit_target_uses_base_harmonics(self):
        # K = [1, 2]: n=1 sits on the base grid unit. No subharmonic divisor
        # of 1 exists, so it must be doubly covered by integer base harmonics
        # (Tempo(h, 1), h ≥ 2 — e.g. 2·r and 3·r), never by the base itself.
        _, K, tempi = subharmonic_cover(
            [Fraction(1, 2), Fraction(1)], pair_with_base=False)
        assert K == [1, 2]
        ratios = {Fraction(t.h, t.a) for t in tempi}
        assert Fraction(1) not in ratios, (tempi, "base tempo smuggled in")
        hitting_one = [t for t in tempi if t.hits(1)]
        assert all(t.h >= 2 and t.a == 1 for t in hitting_one), hitting_one
        assert len(hitting_one) >= 2


class TestSubharmonicCoverReport:
    def test_report_handles_string_input(self):
        rep = subharmonic_cover_report(["3/2", "5/2", "4"])
        assert rep["K"] == [3, 5, 8]

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            subharmonic_cover_report([])
