"""MusicXML export, via music21.

Two entry points:

  - `from_midi_file(path)` renders a finished MIDI file (one track per voice) to
    MusicXML. This is what the app uses to offer a "Download score" of a voice
    result: music21 reads the multi-track MIDI as one part per track and engraves
    it. Onsets/durations are quantized to 16ths + triplet-eighths so the result is
    readable — irrational echo ratios collapse to their nearest notatable value
    (no notation system can represent an irrational rhythm exactly).

  - `serialize(score)` / `save(score, path)` render a model Score directly, for
    symmetry with score_io.serializers.midi.

MusicXML opens in MuseScore, Sibelius, Finale, Dorico, and most notation tools —
no LilyPond anywhere. music21 is imported lazily so the package works without it.
"""
from __future__ import annotations
import os
import tempfile

from model.score import Score


# Quantize grid: 16th notes (4 per quarter) and triplet-eighths (3 per quarter).
# This is what makes engraved output readable rather than a mess of tied 64ths.
_QL_DIVISORS = (4, 3)


def _require_m21():
    try:
        import music21 as m21
    except ImportError as e:  # pragma: no cover - exercised only without music21
        raise ImportError("music21 is not installed. Run: pip install music21") from e
    return m21


def _stream_to_bytes(stream) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".musicxml")
    os.close(fd)
    try:
        stream.write("musicxml", fp=tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def from_midi_file(mid_path: str) -> bytes:
    """Render a MIDI file to MusicXML bytes, quantized for readable notation.

    Each MIDI track becomes one part/staff. Raises ImportError if music21 is
    missing; lets music21's own errors propagate for genuinely unreadable input.

    Example: open("out.musicxml", "wb").write(from_midi_file("out.mid"))
    """
    m21 = _require_m21()
    stream = m21.converter.parse(mid_path)
    try:
        stream.quantize(_QL_DIVISORS, inPlace=True)
    except Exception:
        # Quantize is a readability nicety; never fail export over it.
        pass
    return _stream_to_bytes(stream)


def save_from_midi(mid_path: str, out_path: str) -> None:
    """Render a MIDI file to a .musicxml file on disk."""
    with open(out_path, "wb") as f:
        f.write(from_midi_file(mid_path))


def serialize(score: Score) -> bytes:
    """Render a model Score to MusicXML bytes (quantized to 16ths + triplets).

    Raises ImportError if music21 is not installed.
    """
    _require_m21()
    from score_io.serializers.midi import _build_stream

    stream = _build_stream(score)
    try:
        stream.quantize(_QL_DIVISORS, inPlace=True)
    except Exception:
        pass
    return _stream_to_bytes(stream)


def save(score: Score, path: str) -> None:
    """Serialize a Score to a .musicxml file.

    Example: save(score, "piece.musicxml")
    """
    with open(path, "wb") as f:
        f.write(serialize(score))
