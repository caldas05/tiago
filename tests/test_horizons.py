"""Tests for analysis.horizons — the bounded alignment math.

Also enforces the project rule: no module under `analysis/` may import
`math.lcm` directly. Every alignment calculation goes through this module."""
from __future__ import annotations
import ast
from fractions import Fraction
from pathlib import Path

import pytest

from analysis.horizons import (
    bounded_realign_period,
    near_alignments,
    effective_period_label,
    NearAlignment,
    PERCEPTUAL_HORIZON_BEATS_DEFAULT,
)


class TestBoundedRealignPeriod:
    def test_3_2_against_1_realigns_every_3_beats(self):
        # A voice at scale 3/2 against an unscaled (1/1) theme realigns when
        # the scaled voice has stretched by an integer number of theme units;
        # smallest such output-time is lcm(3,2)/2 = 3 beats per theme-unit.
        period = bounded_realign_period(
            Fraction(3, 2), Fraction(1),
            total_beats=Fraction(100),
        )
        assert period == Fraction(3)

    def test_2_against_1_realigns_every_2_beats(self):
        period = bounded_realign_period(
            Fraction(2), Fraction(1),
            total_beats=Fraction(100),
        )
        assert period == Fraction(2)

    def test_equal_scales_realign_immediately(self):
        period = bounded_realign_period(
            Fraction(3, 2), Fraction(3, 2),
            total_beats=Fraction(100),
        )
        assert period == Fraction(3, 2)

    def test_period_exceeding_total_beats_returns_none(self):
        # 7/5 vs 1: lcm(7,5)/5 = 7 beats — fits in 100 but not in 4.
        assert bounded_realign_period(
            Fraction(7, 5), Fraction(1),
            total_beats=Fraction(4),
        ) is None
        assert bounded_realign_period(
            Fraction(7, 5), Fraction(1),
            total_beats=Fraction(100),
        ) == Fraction(7)

    def test_period_exceeding_horizon_returns_none(self):
        # Force horizon below the natural period.
        assert bounded_realign_period(
            Fraction(7, 5), Fraction(1),
            total_beats=Fraction(1000),
            horizon_beats=Fraction(3),
        ) is None

    def test_irrational_scale_collapses_via_denom_cap(self):
        # Fraction approximating sqrt(2). With a tight denom cap it
        # collapses to a small ratio whose period fits inside the horizon.
        sqrt2_approx = Fraction(1414213562, 10**9)
        period = bounded_realign_period(
            sqrt2_approx, Fraction(1),
            total_beats=Fraction(1000),
            horizon_beats=Fraction(1000),
            denom_cap=8,
        )
        assert period is not None
        # 7/5 = 1.4 is sqrt(2)'s best approximation with denom <= 8.
        assert period == Fraction(7)


class TestNearAlignments:
    def test_exact_coincidence_distance_zero(self):
        hits = near_alignments(
            [Fraction(0), Fraction(2), Fraction(4)],
            [Fraction(0), Fraction(3), Fraction(4)],
        )
        # Beats 0 and 4 are exact matches.
        exact = [h for h in hits if h.distance == 0]
        assert {h.beat for h in exact} == {Fraction(0), Fraction(4)}

    def test_within_epsilon_counts(self):
        hits = near_alignments(
            [Fraction(1, 16)],  # tiny offset
            [Fraction(0)],
            epsilon=Fraction(1, 8),
        )
        assert len(hits) == 1
        assert hits[0].distance == Fraction(1, 16)

    def test_outside_epsilon_excluded(self):
        hits = near_alignments(
            [Fraction(1, 2)],
            [Fraction(0)],
            epsilon=Fraction(1, 8),
        )
        assert hits == []

    def test_results_ranked_by_closeness(self):
        hits = near_alignments(
            [Fraction(0), Fraction(1, 16), Fraction(2)],
            [Fraction(1, 32), Fraction(2)],
            epsilon=Fraction(1, 4),
        )
        # First hit should be the closest pair.
        distances = [h.distance for h in hits]
        assert distances == sorted(distances)

    def test_max_hits_truncates(self):
        onsets = [Fraction(k) for k in range(50)]
        hits = near_alignments(onsets, onsets, max_hits=5)
        assert len(hits) == 5


class TestEffectivePeriodLabel:
    def test_periodic(self):
        label = effective_period_label(
            Fraction(3, 2), Fraction(1),
            total_beats=Fraction(100),
        )
        assert label.startswith("periodic@")

    def test_above_horizon(self):
        label = effective_period_label(
            Fraction(7, 5), Fraction(1),
            total_beats=Fraction(1000),
            horizon_beats=Fraction(3),
        )
        assert label == "above_horizon"


class TestNoRawLcmInAnalysisPackage:
    """Project rule: alignment math goes through horizons.py. Catching a
    stray `from math import lcm` in some future analysis/* module is exactly
    the kind of regression a one-line import-scan can prevent cheaply."""

    def test_only_horizons_imports_lcm(self):
        analysis_dir = Path(__file__).parent.parent / "analysis"
        offenders: list[str] = []
        for py in analysis_dir.glob("*.py"):
            if py.name == "horizons.py":
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "math":
                    for alias in node.names:
                        if alias.name == "lcm":
                            offenders.append(f"{py.name}: from math import lcm")
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id == "math" and node.attr == "lcm":
                        offenders.append(f"{py.name}: math.lcm reference")
        assert not offenders, (
            "raw lcm in analysis/* — route through horizons.bounded_realign_period: "
            + ", ".join(offenders)
        )


def test_defaults_are_sane():
    assert PERCEPTUAL_HORIZON_BEATS_DEFAULT > 0
    assert isinstance(NearAlignment(Fraction(0), Fraction(0), Fraction(0), Fraction(0)).beat, Fraction)
