"""AnalysisReport: the JSON-serialisable bundle returned by the analysis
pass. Consumed by the `/analyze` endpoint in app.py (slice 3) and stored in
session logs.

Kept deliberately thin in this slice — alignment, drift, and density fields
will be added by later slices. Each future field gets its own optional key
on the report so older logs stay parseable.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from fractions import Fraction
import json
from typing import Any

from model.score import Score
from model.events import Note, Rest, Chord
from model.voice import Voice
from dataclasses import replace

from analysis.grid import build_score_grid, ScoreGrid
from analysis.intervals import all_pair_intervals, PairIntervals


@dataclass(frozen=True)
class AnalysisReport:
    ticks_per_beat: int
    total_beats: str  # serialised Fraction
    snap_count: int
    voice_ids: tuple[str, ...]
    pair_intervals: tuple[PairIntervals, ...]
    # Reserved for future slices; declared so old reports round-trip.
    alignment: dict[str, Any] = field(default_factory=dict)
    drift: dict[str, Any] = field(default_factory=dict)
    density: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(_asdict_safe(self), indent=2)


def _asdict_safe(obj: Any) -> Any:
    """Like dataclasses.asdict but tolerates Fraction (→ str) and tuple."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _asdict_safe(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Fraction):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_asdict_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _asdict_safe(v) for k, v in obj.items()}
    return obj


def _voices_from_score(score: Score) -> list[Voice]:
    """Collect one absolute-offset Voice per *voice id* across the whole
    score, regardless of how parts/measures are organised.

    `load_mido` packs all MIDI tracks into a single Part whose Measures hold
    one Voice per track-name. `polytime` produces one Part per voice. Both
    layouts are flattened the same way here: group events by voice id, keep
    absolute offsets, drop rests."""
    by_id: dict[str, list] = {}
    for part in score.parts:
        cum = Fraction(0)
        for m in part.measures:
            for v in m.voices:
                for ev in v.events:
                    if isinstance(ev, Rest):
                        continue
                    by_id.setdefault(v.id, []).append(
                        replace(ev, offset=cum + ev.offset)
                    )
            cum += m.time_signature.beats_per_measure
    return [
        Voice(id=vid, events=tuple(sorted(evs, key=lambda e: e.offset)))
        for vid, evs in by_id.items()
        if evs
    ]


def analyze_midi_file(path: str) -> AnalysisReport:
    """Load a MIDI file and analyse it. Convenience wrapper used by the
    `/analyze` endpoint and by `_handle_process` after writing the output
    file. Imports `load_mido` lazily so importing `analysis.report` doesn't
    pull mido at module-load time (matters for PyInstaller cold-start)."""
    from score_io.live.midi_file import load_mido
    score = load_mido(str(path))
    return analyze_score(score)


def analyze_score(score: Score) -> AnalysisReport:
    """Build a report from a polytime output Score (one Part per voice)."""
    voices = _voices_from_score(score)
    grid: ScoreGrid = build_score_grid(voices)
    pairs = all_pair_intervals(grid)
    return AnalysisReport(
        ticks_per_beat=grid.ticks_per_beat,
        total_beats=str(grid.total_beats),
        snap_count=grid.snap_count,
        voice_ids=tuple(v.voice_id for v in grid.voices),
        pair_intervals=pairs,
    )
