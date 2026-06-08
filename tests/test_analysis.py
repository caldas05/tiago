"""Tests for the slice-2 analysis pass: grid + intervals + minimal report."""
from __future__ import annotations
import json
from fractions import Fraction

import pytest

from model.duration import Duration
from model.events import Note, Chord
from model.pitch import Pitch
from model.voice import Voice
from model.measure import Measure, TimeSignature
from model.part import Part
from model.score import Score

from analysis.grid import build_score_grid, choose_ticks_per_beat
from analysis.intervals import all_pair_intervals, DEFAULT_DISSONANCE_WEIGHTS
from analysis.report import analyze_score, AnalysisReport


def _q(midi: int, offset: int | Fraction, beats: int | Fraction = 1) -> Note:
    return Note(
        duration=Duration(value=Fraction(beats)),
        offset=Fraction(offset),
        pitch=Pitch(midi=midi, spelling="?", octave=midi // 12 - 1),
    )


def _v(vid: str, *notes: Note) -> Voice:
    return Voice(id=vid, events=tuple(notes))


# --- grid -------------------------------------------------------------------

class TestGrid:
    def test_unit_grid_for_integer_offsets(self):
        v = _v("a", _q(60, 0), _q(62, 1), _q(64, 2))
        g = build_score_grid([v])
        assert g.ticks_per_beat == 1
        assert g.total_beats == Fraction(3)
        assert g.voices[0].pitches[0] == frozenset({60})
        assert g.voices[0].pitches[1] == frozenset({62})

    def test_half_beat_offsets_double_resolution(self):
        v = _v("a", _q(60, 0), _q(62, Fraction(1, 2)))
        g = build_score_grid([v])
        assert g.ticks_per_beat == 2

    def test_denominator_cap_caps_resolution(self):
        v = _v("a", _q(60, Fraction(1, 128)))
        g = build_score_grid([v], cap=16)
        assert g.ticks_per_beat == 16
        assert g.snap_count >= 1

    def test_chord_pitches_all_present(self):
        chord = Chord(
            duration=Duration(value=Fraction(1)),
            offset=Fraction(0),
            pitches=(
                Pitch(midi=60, spelling="C", octave=4),
                Pitch(midi=64, spelling="E", octave=4),
                Pitch(midi=67, spelling="G", octave=4),
            ),
        )
        v = Voice(id="a", events=(chord,))
        g = build_score_grid([v])
        assert g.voices[0].pitches[0] == frozenset({60, 64, 67})

    def test_empty_input(self):
        g = build_score_grid([])
        assert g.total_beats == Fraction(0)
        assert g.voices == ()


# --- intervals --------------------------------------------------------------

class TestIntervals:
    def test_identical_voices_are_all_unison(self):
        v = _v("a", _q(60, 0), _q(62, 1))
        w = _v("b", _q(60, 0), _q(62, 1))
        g = build_score_grid([v, w])
        (pair,) = all_pair_intervals(g)
        # Every overlap tick contributes one pc-diff of 0.
        assert pair.histogram[0] > 0
        assert sum(pair.histogram[1:]) == 0
        # Unison weight is 0 → dissonance curve is flat zero where both play.
        assert all(x == 0 for x in pair.dissonance_curve)

    def test_tritone_transpose_lands_in_bin_6(self):
        v = _v("a", _q(60, 0, 2))
        w = _v("b", _q(66, 0, 2))  # +6 semitones
        g = build_score_grid([v, w])
        (pair,) = all_pair_intervals(g)
        assert pair.histogram[6] > 0
        assert pair.histogram[0] == 0
        # Tritone weight is 1.0 in defaults.
        assert max(pair.dissonance_curve) == pytest.approx(1.0)

    def test_perfect_fifth_lands_in_p4_p5_bins(self):
        # Histogram bins are pc-diffs (voice_i - voice_j) mod 12. A perfect
        # fifth between the two voices fires bin 5 or bin 7 depending on
        # which voice is the lower one — both are the P4/P5 consonance pair.
        v = _v("a", _q(60, 0, 2))
        w = _v("b", _q(67, 0, 2))
        g = build_score_grid([v, w])
        (pair,) = all_pair_intervals(g)
        assert pair.histogram[5] + pair.histogram[7] > 0
        assert pair.histogram[6] == 0

    def test_no_overlap_no_histogram(self):
        v = _v("a", _q(60, 0))
        w = _v("b", _q(60, 5))  # far apart, no temporal overlap
        g = build_score_grid([v, w])
        (pair,) = all_pair_intervals(g)
        assert sum(pair.histogram) == 0
        assert pair.overlap_ticks == 0

    def test_single_voice_chord_yields_intra_voice_pair(self):
        # A C-E-G chord in one voice should produce a self-pair with
        # intervals m3 (3), M3 (4), P5 (7) — and no cross-voice pairs.
        chord = Chord(
            duration=Duration(value=Fraction(1)),
            offset=Fraction(0),
            pitches=(
                Pitch(midi=60, spelling="C", octave=4),
                Pitch(midi=64, spelling="E", octave=4),
                Pitch(midi=67, spelling="G", octave=4),
            ),
        )
        v = Voice(id="solo", events=(chord,))
        g = build_score_grid([v])
        pairs = all_pair_intervals(g)
        assert len(pairs) == 1
        (self_pair,) = pairs
        assert self_pair.voice_i == self_pair.voice_j == "solo"
        # Intervals present in a major triad: m3 (3), M3 (4), P5 (7).
        assert self_pair.histogram[3] > 0
        assert self_pair.histogram[4] > 0
        assert self_pair.histogram[7] > 0
        # No tritone in a major triad.
        assert self_pair.histogram[6] == 0

    def test_intra_voice_skipped_when_no_chords(self):
        # A monophonic voice has nothing to clash with internally.
        v = _v("solo", _q(60, 0), _q(62, 1), _q(64, 2))
        g = build_score_grid([v])
        pairs = all_pair_intervals(g)
        assert pairs == ()

    def test_three_voices_yields_three_pairs(self):
        a = _v("a", _q(60, 0, 2))
        b = _v("b", _q(64, 0, 2))
        c = _v("c", _q(67, 0, 2))
        g = build_score_grid([a, b, c])
        pairs = all_pair_intervals(g)
        assert len(pairs) == 3
        assert {(p.voice_i, p.voice_j) for p in pairs} == {
            ("a", "b"), ("a", "c"), ("b", "c"),
        }


# --- report integration -----------------------------------------------------

def _score_from_voices(*voices: Voice) -> Score:
    ts = TimeSignature(4, 4)
    parts = []
    for v in voices:
        m = Measure(number=1, time_signature=ts, voices=(v,))
        parts.append(Part(name=v.id, instrument=None, clef="treble", measures=(m,)))
    return Score(title="t", parts=tuple(parts), metadata={})


class TestReport:
    def test_report_round_trips_through_json(self):
        a = _v("a", _q(60, 0, 2))
        b = _v("b", _q(67, 0, 2))
        score = _score_from_voices(a, b)
        report = analyze_score(score)
        assert isinstance(report, AnalysisReport)
        as_json = report.to_json()
        parsed = json.loads(as_json)
        assert parsed["voice_ids"] == ["a", "b"]
        assert parsed["total_beats"] == "2"
        assert len(parsed["pair_intervals"]) == 1
        # Reserved fields present for forward-compat.
        assert "alignment" in parsed
        assert "drift" in parsed
        assert "density" in parsed
