"""MusicXML export (PR3): model Score / MIDI -> MusicXML.

Round-trips the exported MusicXML back through the PR2 parser to prove the
notes survive. Hermetic — no corpus, no network.
"""
from __future__ import annotations
import os
import tempfile
from fractions import Fraction

import pytest

from model.pitch import Pitch
from model.duration import Duration
from model.events import Note
from model.voice import Voice
from model.measure import Measure, TimeSignature
from model.part import Part
from model.score import Score

pytest.importorskip("music21")
from score_io.serializers.musicxml import serialize, save, from_midi_file  # noqa: E402
from score_io.parsers.musicxml import parse  # noqa: E402
from score_io.live.midi_file import save_mido  # noqa: E402


_NOTES = [(60, "C", 4), (64, "E", 4), (67, "G", 4), (72, "C", 5)]
_EXPECTED = [(Fraction(i), m) for i, (m, _, _) in enumerate(_NOTES)]


def _quarter_score() -> Score:
    notes = tuple(
        Note(duration=Duration(value=Fraction(1)), offset=Fraction(i),
             pitch=Pitch(midi=m, spelling=s, octave=o))
        for i, (m, s, o) in enumerate(_NOTES)
    )
    voice = Voice(id="rh", events=notes)
    measure = Measure(number=1, time_signature=TimeSignature(4, 4), voices=(voice,))
    part = Part(name="Right", instrument=None, clef="treble", measures=(measure,))
    return Score(title="Quartet", parts=(part,), metadata={})


def _onsets(score: Score):
    out = []
    for part in score.parts:
        cum = Fraction(0)
        for m in part.measures:
            for v in m.voices:
                for e in v.events:
                    if hasattr(e, "pitch"):
                        out.append((cum + e.offset, e.pitch.midi))
            cum += m.time_signature.beats_per_measure
    return sorted(out)


def _reparse(xml: bytes) -> Score:
    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False) as t:
        t.write(xml)
        path = t.name
    try:
        return parse(path)
    finally:
        os.unlink(path)


def test_serialize_is_valid_musicxml():
    xml = serialize(_quarter_score())
    assert b"score-partwise" in xml or b"score-timewise" in xml


def test_serialize_round_trips_through_parser():
    assert _onsets(_reparse(serialize(_quarter_score()))) == _EXPECTED


def test_save_writes_a_file(tmp_path):
    out = tmp_path / "piece.musicxml"
    save(_quarter_score(), str(out))
    assert out.exists() and out.stat().st_size > 0


def test_from_midi_file_round_trips(tmp_path):
    mid = tmp_path / "q.mid"
    save_mido(_quarter_score(), str(mid))
    assert _onsets(_reparse(from_midi_file(str(mid)))) == _EXPECTED
