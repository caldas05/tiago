"""Score import: MusicXML -> model -> one MIDI per voice (PR2).

Uses hand-written MusicXML fixtures so the tests are hermetic — no music21
corpus, no network. Covers voice separation across parts and staves, the
fragment-voice merge policy, chord handling, and exact onset/pitch round-trip
through split + load_mido.
"""
from __future__ import annotations
import tempfile
from fractions import Fraction

import pytest

from model.events import Note, Chord
from model.measure import TimeSignature
from score_io.live.midi_file import load_mido

musicxml = pytest.importorskip("score_io.parsers.musicxml")
from score_io.parsers.musicxml import parse, ParseError  # noqa: E402
from score_io.split import split_voices  # noqa: E402


_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN"
 "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">"""


def _note(step, octv, dur, voice, typ, chord=False):
    c = "<chord/>" if chord else ""
    return (f'<note>{c}<pitch><step>{step}</step><octave>{octv}</octave></pitch>'
            f'<duration>{dur}</duration><voice>{voice}</voice><type>{typ}</type></note>')


# Flute (one voice) + Piano (one staff, TWO substantial voices that both span
# the whole excerpt). Voice 1 ends on a C+E chord.
FIXTURE_SEPARATE = _HEAD + f"""
  <part-list>
    <score-part id="P1"><part-name>Flute</part-name></score-part>
    <score-part id="P2"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      {_note('C',4,1,1,'quarter')}{_note('D',4,1,1,'quarter')}{_note('E',4,2,1,'half')}
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      {_note('C',5,1,1,'quarter')}{_note('D',5,1,1,'quarter')}
      {_note('E',5,1,1,'quarter')}{_note('F',5,1,1,'quarter')}
      <backup><duration>4</duration></backup>
      {_note('C',4,1,2,'quarter')}{_note('E',4,1,2,'quarter')}
      {_note('G',4,1,2,'quarter')}{_note('E',4,1,2,'quarter')}
    </measure>
    <measure number="2">
      {_note('G',5,1,1,'quarter')}{_note('F',5,1,1,'quarter')}
      {_note('E',5,2,1,'half')}{_note('G',5,2,1,'half',chord=True)}
      <backup><duration>4</duration></backup>
      {_note('D',4,1,2,'quarter')}{_note('F',4,1,2,'quarter')}
      {_note('A',4,2,2,'half')}
    </measure>
  </part>
</score-partwise>
"""

# Piano, one staff: a main voice across both bars + a 2-note inner voice that
# appears only in bar 2. The inner voice is a fragment → folded into the main.
FIXTURE_FRAGMENT = _HEAD + f"""
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      {_note('C',5,1,1,'quarter')}{_note('D',5,1,1,'quarter')}
      {_note('E',5,1,1,'quarter')}{_note('F',5,1,1,'quarter')}
    </measure>
    <measure number="2">
      {_note('G',5,1,1,'quarter')}{_note('F',5,1,1,'quarter')}
      {_note('E',5,1,1,'quarter')}{_note('D',5,1,1,'quarter')}
      <backup><duration>4</duration></backup>
      {_note('G',4,2,2,'half')}{_note('A',4,2,2,'half')}
    </measure>
  </part>
</score-partwise>
"""


def _write(tmp_path, xml, name="fixture.musicxml"):
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return str(p)


def _pitched(part):
    return [e for m in part.measures for v in m.voices for e in v.events
            if isinstance(e, (Note, Chord))]


def _onsets(data):
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as t:
        t.write(data)
        path = t.name
    sc = load_mido(path, time_signature=TimeSignature(4, 4))
    out = []
    cum = Fraction(0)
    for m in sc.parts[0].measures:
        for v in m.voices:
            for e in v.events:
                if isinstance(e, Note):
                    out.append((cum + e.offset, e.pitch.midi))
                elif isinstance(e, Chord):
                    for p in e.pitches:
                        out.append((cum + e.offset, p.midi))
        cum += m.time_signature.beats_per_measure
    return sorted(out)


def test_substantial_voices_stay_separate(tmp_path):
    score = parse(_write(tmp_path, FIXTURE_SEPARATE))
    assert [p.name for p in score.parts] == ["Flute", "Piano v1", "Piano v2"]
    by_name = {p.name: _pitched(p) for p in score.parts}
    assert len(by_name["Flute"]) == 3
    assert len(by_name["Piano v1"]) == 7     # 6 notes + a final 2-note chord event
    assert len(by_name["Piano v2"]) == 7
    chord = by_name["Piano v1"][-1]
    assert isinstance(chord, Chord)
    assert sorted(p.midi for p in chord.pitches) == [76, 79]  # E5, G5


def test_fragment_voice_is_folded_into_main(tmp_path):
    score = parse(_write(tmp_path, FIXTURE_FRAGMENT))
    # The 2-note inner voice does NOT become its own source.
    assert [p.name for p in score.parts] == ["Piano"]
    # ...but its notes survive, merged into the single Piano source.
    blobs = dict(split_voices(score))
    got = _onsets(blobs["Piano.mid"])
    assert (Fraction(4), 67) in got   # fragment G4 @ bar 2
    assert (Fraction(6), 69) in got   # fragment A4 @ bar 2
    assert len([1 for _, midi in got]) == 10  # 8 main + 2 fragment


def test_split_voices_emits_one_midi_per_surviving_voice(tmp_path):
    score = parse(_write(tmp_path, FIXTURE_SEPARATE))
    blobs = split_voices(score)
    assert [name for name, _ in blobs] == ["Flute.mid", "Piano v1.mid", "Piano v2.mid"]
    assert all(len(data) > 0 for _, data in blobs)


def test_split_round_trip_preserves_onsets_and_pitches(tmp_path):
    score = parse(_write(tmp_path, FIXTURE_SEPARATE))
    blobs = dict(split_voices(score))
    assert _onsets(blobs["Flute.mid"]) == [
        (Fraction(0), 60), (Fraction(1), 62), (Fraction(2), 64),
    ]
    assert _onsets(blobs["Piano v1.mid"]) == [
        (Fraction(0), 72), (Fraction(1), 74), (Fraction(2), 76), (Fraction(3), 77),
        (Fraction(4), 79), (Fraction(5), 77),
        (Fraction(6), 76), (Fraction(6), 79),   # E5+G5 chord
    ]
    assert _onsets(blobs["Piano v2.mid"]) == [
        (Fraction(0), 60), (Fraction(1), 64), (Fraction(2), 67), (Fraction(3), 64),
        (Fraction(4), 62), (Fraction(5), 65), (Fraction(6), 69),
    ]


def test_parse_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.musicxml"
    bad.write_text("this is not a score", encoding="utf-8")
    with pytest.raises(ParseError):
        parse(str(bad))
