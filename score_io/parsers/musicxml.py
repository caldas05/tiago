"""Score → model.Score parser, via music21 (MusicXML, .mxl, MEI, ABC, …).

Goal: get every note into the model with voice separation preserved, so a
dropped score can be exploded into one MIDI per voice (see score_io.split).

This is a *notes-into-MIDI* parser, not a notation-faithful one:

  - Durations are stored as their real beat value (quarterLength). No dots or
    tuplet brackets are recovered — `Duration(value=ql)` has the right
    `actual_beats`, which is all the MIDI writer needs.
  - Each independent line becomes its own model Part. music21 already splits a
    grand staff into one Part per staff (a PianoStaff → two "Piano" parts), and
    within a staff each substantial music21 Voice becomes a separate Part too.
    So an SATB chorale yields 4 parts; a fugue on two staves keeps its voices.
  - Within a staff, *fragment* voices (a brief inner line, the lower notes of a
    couple of split chords, an exporter artifact) are folded back into the
    staff's dominant voice rather than surfacing as their own confusing source.
    See `_merge_fragments`.

music21 is imported lazily so the rest of the package works without it.
"""
from __future__ import annotations
from collections import Counter, OrderedDict
from dataclasses import replace
from fractions import Fraction

from model.pitch import Pitch
from model.duration import Duration
from model.events import Note, Rest, Chord, Event
from model.voice import Voice
from model.measure import Measure, TimeSignature
from model.part import Part
from model.score import Score


class ParseError(Exception):
    pass


# Cap for rationalizing music21 offsets/durations. MusicXML quarterLengths are
# already exact rationals; this just guards against float noise from odd files.
_MAX_DEN = 960

# A staff's inner "fragment" voices are folded into the staff's dominant voice
# instead of becoming their own source. A voice is a fragment if it is small by
# any of three measures: absolute note count, share of the staff's notes, or how
# few of the staff's active bars it touches (the last catches an inner line that
# appears in only a bar or two). A voice that is substantial by all three stays
# separate, so genuine counterpoint (e.g. a fugue on two staves) is preserved.
_FRAG_ABS = 4       # <= this many pitched notes -> fragment
_FRAG_FRAC = 0.20   # < this share of the staff's pitched notes -> fragment
_FRAG_SPAN = 0.34   # present in < this share of the staff's active bars -> fragment


def _require_converter():
    try:
        import music21.converter as converter
    except ImportError as e:  # pragma: no cover - exercised only without music21
        raise ParseError("music21 is not installed. Run: pip install music21") from e
    return converter


def _m21_pitch(p) -> Pitch:
    octave = p.octave if p.octave is not None else p.midi // 12 - 1
    spelling = p.name.replace("-", "b")  # music21 spells flats 'B-'; model wants 'Bb'
    return Pitch(midi=p.midi, spelling=spelling, octave=octave)


def _m21_event(el, offset: Fraction) -> Event | None:
    """Convert one music21 note/chord/rest to a model Event, or None to skip
    (grace notes, zero-length, unpitched percussion)."""
    ql = Fraction(el.duration.quarterLength).limit_denominator(_MAX_DEN)
    if ql <= 0:
        return None
    dur = Duration(value=ql)
    if el.isRest:
        return Rest(duration=dur, offset=offset)
    if el.isChord:
        pitches = tuple(_m21_pitch(p) for p in el.pitches)
        if len(pitches) < 2:
            return Note(duration=dur, offset=offset, pitch=pitches[0]) if pitches else None
        return Chord(duration=dur, offset=offset, pitches=pitches)
    if getattr(el, "isNote", False):
        return Note(duration=dur, offset=offset, pitch=_m21_pitch(el.pitch))
    return None


def _detect_ts(score_m21) -> TimeSignature:
    import music21.meter as meter
    tss = list(score_m21.recurse().getElementsByClass(meter.TimeSignature))
    if tss:
        return TimeSignature(tss[0].numerator, tss[0].denominator)
    return TimeSignature(4, 4)


def _pitched(events) -> int:
    return sum(1 for e in events if isinstance(e, (Note, Chord)))


def _collect_staff_voices(part):
    """Read one staff into per-voice event lists with absolute offsets.

    Returns (voices, voice_bars, n_active_bars):
      - voices: ordered {voice_id -> [Event]} in first-appearance order.
      - voice_bars: {voice_id -> set(bar_index)} for bars carrying pitched notes.
      - n_active_bars: how many bars carry any pitched note (the denominator for
        a voice's measure coverage).
    """
    import music21.stream as m21stream
    measures = list(part.getElementsByClass(m21stream.Measure))
    flat = not measures
    if flat:
        measures = [part]  # notes live directly on the part

    voices: "OrderedDict[str, list[Event]]" = OrderedDict()
    voice_bars: dict[str, set[int]] = {}
    active: set[int] = set()
    for bar_i, m in enumerate(measures):
        m_start = Fraction(0) if flat else Fraction(m.offset).limit_denominator(_MAX_DEN)
        vs = list(getattr(m, "voices", []) or [])
        streams = [(str(v.id), v) for v in vs] if vs else [("1", m)]
        for vid, stream in streams:
            bkt = voices.setdefault(vid, [])
            for el in stream.notesAndRests:
                ev = _m21_event(
                    el, m_start + Fraction(el.offset).limit_denominator(_MAX_DEN)
                )
                if ev is None:
                    continue
                bkt.append(ev)
                if isinstance(ev, (Note, Chord)):
                    voice_bars.setdefault(vid, set()).add(bar_i)
                    active.add(bar_i)
    return voices, voice_bars, len(active)


def _merge_fragments(voices, voice_bars, n_active) -> list[list[Event]]:
    """Fold fragment voices into the staff's dominant voice.

    Returns the surviving voices' event lists (ids dropped; the caller renumbers
    them v1..vk). A single-voice staff is returned untouched. If every voice is a
    fragment, they all collapse into one.
    """
    items = list(voices.items())
    if len(items) <= 1:
        return [evs for _, evs in items]

    counts = {vid: _pitched(evs) for vid, evs in items}
    total = sum(counts.values())
    if total == 0:
        return [evs for _, evs in items]

    def is_fragment(vid: str) -> bool:
        c = counts[vid]
        if c == 0:
            return True
        cover = len(voice_bars.get(vid, ())) / n_active if n_active else 1.0
        return c <= _FRAG_ABS or c < _FRAG_FRAC * total or cover < _FRAG_SPAN

    sub_vids = [vid for vid, _ in items if not is_fragment(vid)]
    frag_notes = [
        e
        for vid, evs in items
        if vid not in sub_vids
        for e in evs
        if isinstance(e, (Note, Chord))
    ]
    if not sub_vids:
        # Everything is a fragment — collapse to a single voice of all the notes.
        return [frag_notes]

    dominant = max(sub_vids, key=lambda v: counts[v])
    out: list[list[Event]] = []
    for vid, evs in items:
        if vid not in sub_vids:
            continue  # merged into `dominant`
        out.append(evs + frag_notes if vid == dominant else list(evs))
    return out


def _bin_into_measures(events: list[Event], ts: TimeSignature) -> tuple[Measure, ...]:
    """Re-bin absolute-offset events into uniform measures of `ts`.

    Binning and the downstream MIDI writer both use `ts.beats_per_measure`, so
    timing reconstructs exactly regardless of the source's barring (the absolute
    offsets were taken from music21 measure positions, which already account for
    pickups).
    """
    cap = ts.beats_per_measure
    if not events:
        rest = Rest(duration=Duration(value=cap), offset=Fraction(0))
        return (Measure(number=1, time_signature=ts, voices=(Voice(id="1", events=(rest,)),)),)
    last = max(e.offset + e.duration.actual_beats for e in events)
    n = max(1, int(-(-last // cap)))  # ceil
    measures: list[Measure] = []
    for i in range(n):
        lo = cap * i
        hi = lo + cap
        local = tuple(
            replace(e, offset=e.offset - lo) for e in events if lo <= e.offset < hi
        )
        if not local:
            local = (Rest(duration=Duration(value=cap), offset=Fraction(0)),)
        measures.append(
            Measure(number=i + 1, time_signature=ts, voices=(Voice(id="1", events=local),))
        )
    return tuple(measures)


def parse(path: str) -> Score:
    """Parse a score file into a Score with one Part per independent voice.

    Each Part is named for its source line ("Soprano", "Piano [staff 2] v1", …)
    so score_io.split.split_voices and the UI can label sources meaningfully.
    Fragment voices within a staff are folded into the staff's main line.

    Raises ParseError on unreadable input or a score with no notes.

    Example: parse("chorale.musicxml") → Score with 4 single-voice parts.
    """
    converter = _require_converter()
    try:
        s = converter.parse(path)
    except Exception as e:
        raise ParseError(f"cannot parse score '{path}': {e}") from e

    ts = _detect_ts(s)
    m21_parts = list(s.parts) or [s]

    # Disambiguate parts that share a name (a grand staff is two "Piano" parts).
    base_labels = [
        (getattr(p, "partName", None) or f"Part {i + 1}") for i, p in enumerate(m21_parts)
    ]
    label_counts = Counter(base_labels)
    staff_seen: Counter = Counter()

    parts_out: list[Part] = []
    for pi, part in enumerate(m21_parts):
        base = base_labels[pi]
        if label_counts[base] > 1:
            staff_seen[base] += 1
            base = f"{base} [staff {staff_seen[base]}]"

        voices, voice_bars, n_active = _collect_staff_voices(part)
        survivors = [
            evs for evs in _merge_fragments(voices, voice_bars, n_active)
            if any(isinstance(e, (Note, Chord)) for e in evs)
        ]
        if not survivors:
            continue

        labels = [base] if len(survivors) == 1 else [
            f"{base} v{k + 1}" for k in range(len(survivors))
        ]
        for events, label in zip(survivors, labels):
            events = sorted(events, key=lambda e: e.offset)
            measures = _bin_into_measures(events, ts)
            # Stamp each voice's id with the label so save_mido names the track.
            measures = tuple(
                replace(m, voices=tuple(replace(v, id=label) for v in m.voices))
                for m in measures
            )
            parts_out.append(Part(name=label, instrument=None, clef="treble", measures=measures))

    if not parts_out:
        raise ParseError("no notes found in score")

    md = getattr(s, "metadata", None)
    title = (getattr(md, "title", None) if md else None) or "Score"
    return Score(title=str(title), parts=tuple(parts_out), metadata={})
