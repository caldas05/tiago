"""Tests for transforms.pitch_dsl — per-voice pitch op parser used by polytime."""
from __future__ import annotations
from fractions import Fraction

import pytest

from model.duration import Duration
from model.events import Note
from model.pitch import Pitch
from model.voice import Voice

from transforms.pitch_dsl import (
    apply_pitch_op, parse_op_chain, parse_op, parse_pitch, parse_scale_token,
)


def _q(midi: int, offset: int = 0) -> Note:
    return Note(
        duration=Duration(value=Fraction(1)),
        offset=Fraction(offset),
        pitch=Pitch(midi=midi, spelling="?", octave=midi // 12 - 1),
    )


def _v(*notes: Note) -> Voice:
    return Voice(id="t", events=tuple(notes))


class TestParsePitch:
    def test_natural(self):
        assert parse_pitch("C4").midi == 60
        assert parse_pitch("A4").midi == 69

    def test_sharp_and_flat(self):
        assert parse_pitch("C#4").midi == 61
        assert parse_pitch("Bb3").midi == 58
        assert parse_pitch("F#5").midi == 78

    def test_negative_octave(self):
        # C-1 = midi 0
        assert parse_pitch("C-1").midi == 0

    def test_bad_pitch_raises(self):
        with pytest.raises(ValueError):
            parse_pitch("H4")
        with pytest.raises(ValueError):
            parse_pitch("C")


class TestIdentity:
    def test_empty_is_identity(self):
        v = _v(_q(60), _q(64, 1))
        assert apply_pitch_op(v, "").events == v.events
        assert apply_pitch_op(v, None).events == v.events
        assert apply_pitch_op(v, "_").events == v.events


class TestTranspose:
    def test_positive(self):
        v = _v(_q(60))
        out = apply_pitch_op(v, "t+12")
        assert out.events[0].pitch.midi == 72

    def test_negative(self):
        v = _v(_q(60))
        out = apply_pitch_op(v, "t-5")
        assert out.events[0].pitch.midi == 55

    def test_no_sign_means_positive(self):
        v = _v(_q(60))
        assert apply_pitch_op(v, "t7").events[0].pitch.midi == 67


class TestChromaticInvert:
    def test_inversion_around_c4_is_involution(self):
        v = _v(_q(60), _q(67), _q(72))
        once = apply_pitch_op(v, "i@C4")
        twice = apply_pitch_op(once, "i@C4")
        assert [e.pitch.midi for e in twice.events] == [60, 67, 72]

    def test_inversion_maps_correctly(self):
        # axis C4=60; G4=67 (axis+7) → F3=53 (axis-7)
        v = _v(_q(67))
        out = apply_pitch_op(v, "i@C4")
        assert out.events[0].pitch.midi == 53


class TestDiatonicInvert:
    def test_c_major_around_e4(self):
        # D4 (degree 2, one below axis E4 deg 3) → F4 (degree 4, one above)
        v = _v(Note(
            duration=Duration(value=Fraction(1)),
            offset=Fraction(0),
            pitch=parse_pitch("D4"),
        ))
        out = apply_pitch_op(v, "id@E4/C-major")
        assert out.events[0].pitch.midi == parse_pitch("F4").midi


class TestChaining:
    def test_chain_applies_left_to_right(self):
        v = _v(_q(60))
        # t+5 then i@C4: 60 → 65 → 2*60 - 65 = 55
        assert apply_pitch_op(v, "t+5;i@C4").events[0].pitch.midi == 55

    def test_chain_with_identity_no_op(self):
        v = _v(_q(60))
        assert apply_pitch_op(v, "_;t+7;_").events[0].pitch.midi == 67


class TestErrors:
    def test_unknown_op(self):
        with pytest.raises(ValueError, match="unrecognised pitch op"):
            parse_op("zz")

    def test_diatonic_invert_missing_scale(self):
        with pytest.raises(ValueError, match="diatonic invert"):
            parse_op("id@C4")

    def test_unknown_scale(self):
        with pytest.raises(ValueError, match="unknown scale"):
            parse_scale_token("C-bogus")


class TestPolytimeIntegration:
    """Cheapest end-to-end: ensure polytime() accepts pitch_ops and the
    transpose semitones land in the rendered voice."""

    def test_pitch_ops_applied(self, tmp_path):
        import mido
        from polytime import polytime
        from model.measure import TimeSignature
        src = tmp_path / "src.mid"
        mf = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack(); mf.tracks.append(tr)
        # one C4 quarter
        tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
        mf.save(str(src))

        out = tmp_path / "out.mid"
        polytime(
            src, at="1b", scales=(Fraction(1),), ats=(Fraction(0),),
            out=out, time_signature=TimeSignature(4, 4),
            combine=False, pitch_ops=("t+7",),
        )
        notes = [m.note for tr in mido.MidiFile(str(out)).tracks for m in tr
                 if m.type == "note_on" and m.velocity > 0]
        # ratio-1 echo of one C4 with t+7 → one G4
        assert notes == [67]

    def test_length_mismatch_raises(self, tmp_path):
        from polytime import polytime
        from model.measure import TimeSignature
        import mido
        src = tmp_path / "src.mid"
        mf = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack(); mf.tracks.append(tr)
        tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
        tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
        mf.save(str(src))
        with pytest.raises(ValueError, match="pitch_ops length"):
            polytime(
                src, scales=(Fraction(1), Fraction(2)),
                ats=(Fraction(0), Fraction(4)),
                out=tmp_path / "o.mid", time_signature=TimeSignature(4, 4),
                pitch_ops=("t+7",),
            )
