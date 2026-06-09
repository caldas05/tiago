"""Explode a multi-voice Score into one single-voice MIDI per voice.

Each output MIDI is exactly what the user would get if they had dropped that
one voice in isolation, so the existing single-source polytime pipeline runs
once per result with no engine changes. Pairs with score_io.parsers.musicxml:
parse a dropped score, split it here, register each blob as its own UI source.
"""
from __future__ import annotations
import os
import tempfile
from dataclasses import replace
from fractions import Fraction

from model.duration import Duration
from model.events import Rest
from model.voice import Voice
from model.part import Part
from model.score import Score
from score_io.live.midi_file import save_mido


def _to_midi_bytes(score: Score) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        save_mido(score, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def split_voices(score: Score) -> list[tuple[str, bytes]]:
    """Return one (name, midi_bytes) per voice across every part.

    Voices are emitted in first-appearance order. A voice's empty measures are
    padded with a full-bar rest so absolute timing survives. The name is the
    part name when it already identifies the voice (as parser output does),
    else "<part>_<voice>"; either way it carries a ".mid" suffix for the UI.

    Example: split_voices(parse("chorale.musicxml")) → 4 entries (S, A, T, B).
    """
    out: list[tuple[str, bytes]] = []
    for part in score.parts:
        voice_ids: list[str] = []
        seen: set[str] = set()
        for m in part.measures:
            for v in m.voices:
                if v.id not in seen:
                    seen.add(v.id)
                    voice_ids.append(v.id)

        for vid in voice_ids:
            new_measures = []
            for m in part.measures:
                kept = tuple(v for v in m.voices if v.id == vid)
                if not kept:
                    cap = m.time_signature.beats_per_measure
                    kept = (
                        Voice(id=vid, events=(
                            Rest(duration=Duration(value=cap), offset=Fraction(0)),
                        )),
                    )
                new_measures.append(replace(m, voices=kept))

            sub = Score(
                title=f"{part.name}/{vid}",
                parts=(Part(
                    name=part.name,
                    instrument=part.instrument,
                    clef=part.clef,
                    measures=tuple(new_measures),
                    extra_staff_clefs=part.extra_staff_clefs,
                ),),
                metadata={},
            )
            label = part.name if part.name == vid else f"{part.name}_{vid}"
            out.append((f"{label}.mid", _to_midi_bytes(sub)))
    return out
