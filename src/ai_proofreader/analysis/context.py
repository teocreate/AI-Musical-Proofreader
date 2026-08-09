"""The shared analysis context.

Detectors are cheap; the indices they need are not. Recomputing sounding pitch, absolute onsets,
voice streams, local key estimates and vertical slices inside each of a dozen rules is how a
30-second budget becomes a five-minute one. So everything derived is computed once, here, and
handed to every detector read-only.

Laziness matters as much as sharing: a score with one part never needs vertical slices, and a
run with pattern detection disabled never needs the motif index. Both are ``cached_property``.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cached_property

import numpy as np

from ..config import AnalysisConfig
from ..models import (
    Chord,
    Clef,
    KeySignature,
    Measure,
    Note,
    Part,
    Pitch,
    Rest,
    Score,
    TimeSignature,
)
from ..music_theory import AccidentalState, KeyEstimate, estimate_key

__all__ = ["AnalysisContext", "NoteRef", "StreamEntry", "VerticalSlice", "VoiceStream"]


@dataclass(frozen=True)
class NoteRef:
    """A note plus everything about where it lives, so detectors never walk back up the tree."""

    part: Part
    measure: Measure
    note: Note
    absolute_onset: Fraction
    #: Sounding pitch: written pitch with the part's transposition applied.
    sounding: Pitch
    #: Position within its voice stream, or -1 for chord members below the top.
    stream_index: int = -1
    #: Set when this note is a member of a chord rather than a standalone note.
    chord: Chord | None = None

    @property
    def written(self) -> Pitch:
        return self.note.pitch

    @property
    def end(self) -> Fraction:
        return self.absolute_onset + self.note.duration.quarter_length

    @property
    def staff(self) -> int:
        return self.note.staff

    @property
    def voice(self) -> str:
        return self.note.voice

    def locate(self) -> str:
        return f"{self.part.display_name} m.{self.measure.number} v{self.voice}"


@dataclass(frozen=True)
class StreamEntry:
    """One slot in a voice: a note, a chord, or a rest."""

    measure: Measure
    absolute_onset: Fraction
    duration: Fraction
    note: Note | None = None
    chord: Chord | None = None
    rest: Rest | None = None
    #: True when this note only continues a tie from the previous entry; melodic analysis skips
    #: these, because a tied-over note is not a new melodic event.
    tie_continuation: bool = False

    @property
    def is_rest(self) -> bool:
        return self.rest is not None

    @property
    def pitch(self) -> Pitch | None:
        return self.note.pitch if self.note else None


@dataclass
class VoiceStream:
    """All events of one voice on one staff of one part, in time order."""

    part: Part
    staff: int
    voice: str
    entries: list[StreamEntry] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.part.id, self.staff, self.voice)

    def notes(self) -> list[StreamEntry]:
        return [entry for entry in self.entries if entry.note is not None]

    def melodic_runs(self, min_length: int = 2) -> Iterator[list[StreamEntry]]:
        """Contiguous runs of sounding notes, broken by rests.

        Tied continuations are dropped rather than breaking the run: a phrase does not stop
        because a note was written across a barline.
        """
        run: list[StreamEntry] = []
        for entry in self.entries:
            if entry.is_rest:
                if len(run) >= min_length:
                    yield run
                run = []
                continue
            if entry.note is None or entry.tie_continuation:
                continue
            run.append(entry)
        if len(run) >= min_length:
            yield run


@dataclass(frozen=True)
class VerticalSlice:
    """Every note sounding at one instant, across all parts."""

    onset: Fraction
    notes: tuple[NoteRef, ...]

    @cached_property
    def sounding_pitch_classes(self) -> frozenset[int]:
        return frozenset(ref.sounding.pitch_class for ref in self.notes)

    @property
    def is_sparse(self) -> bool:
        return len(self.notes) < 3


class AnalysisContext:
    """Read-only derived view of a score, shared by every detector."""

    def __init__(self, score: Score, config: AnalysisConfig | None = None) -> None:
        self.score = score
        self.config = config or AnalysisConfig()
        self._measure_starts: dict[str, list[Fraction]] = {}
        self._build_measure_starts()

    # -- timing --------------------------------------------------------------------

    def _build_measure_starts(self) -> None:
        """Absolute start time of every measure, per part.

        Uses the *actual* filled length when it exceeds the nominal one, so a score whose bars do
        not add up still gets a monotonic timeline instead of collapsing onto itself.
        """
        for part in self.score.parts:
            starts: list[Fraction] = []
            cursor = Fraction(0)
            for measure in part.measures:
                starts.append(cursor)
                nominal = measure.nominal_length
                actual = max(
                    (measure.voice_length(voice) for voice in measure.voices), default=Fraction(0)
                )
                if measure.implicit or nominal == 0:
                    length = actual or nominal
                else:
                    length = max(nominal, actual)
                cursor += length if length > 0 else Fraction(4)
            self._measure_starts[part.id] = starts

    def measure_start(self, part: Part, measure: Measure) -> Fraction:
        starts = self._measure_starts.get(part.id, [])
        if 0 <= measure.index < len(starts):
            return starts[measure.index]
        return Fraction(0)

    def measure_at(self, part: Part, absolute_onset: Fraction) -> Measure | None:
        """Which measure of ``part`` contains an absolute time."""
        starts = self._measure_starts.get(part.id, [])
        if not starts:
            return None
        index = bisect_right(starts, absolute_onset) - 1
        if 0 <= index < len(part.measures):
            return part.measures[index]
        return None

    # -- note and stream indices ---------------------------------------------------

    @cached_property
    def note_refs(self) -> tuple[NoteRef, ...]:
        """Every sounding note in the score, chord members included."""
        refs: list[NoteRef] = []
        for part in self.score.parts:
            transpose = part.transpose
            for measure in part.measures:
                start = self.measure_start(part, measure)
                for event in measure.events:
                    if isinstance(event, Note):
                        refs.append(
                            NoteRef(
                                part=part,
                                measure=measure,
                                note=event,
                                absolute_onset=start + event.onset,
                                sounding=transpose.apply(event.pitch),
                            )
                        )
                    elif isinstance(event, Chord):
                        for member in event.notes:
                            refs.append(
                                NoteRef(
                                    part=part,
                                    measure=measure,
                                    note=member,
                                    absolute_onset=start + event.onset,
                                    sounding=transpose.apply(member.pitch),
                                    chord=event,
                                )
                            )
        return tuple(refs)

    @cached_property
    def notes_by_measure(self) -> dict[tuple[str, int], tuple[NoteRef, ...]]:
        """(part id, measure index) → its notes.

        Without this index, every detector that walks measure by measure ends up filtering the
        whole note list per measure, which is O(n²) and turns a 30-second budget into minutes on
        an orchestral score. Built once, used by everything.
        """
        grouped: dict[tuple[str, int], list[NoteRef]] = {}
        for ref in self.note_refs:
            grouped.setdefault((ref.part.id, ref.measure.index), []).append(ref)
        return {key: tuple(value) for key, value in grouped.items()}

    @cached_property
    def notes_by_ref(self) -> dict[int, NoteRef]:
        """Element handle → note, for cross-referencing suggestions."""
        return {ref.note.ref: ref for ref in self.note_refs if ref.note.ref >= 0}

    @cached_property
    def streams(self) -> tuple[VoiceStream, ...]:
        """Voice streams, one per (part, staff, voice) that contains anything."""
        streams: dict[tuple[str, int, str], VoiceStream] = {}
        for part in self.score.parts:
            for measure in part.measures:
                start = self.measure_start(part, measure)
                for event in measure.events:
                    key = (part.id, event.staff, event.voice)
                    stream = streams.get(key)
                    if stream is None:
                        stream = VoiceStream(part=part, staff=event.staff, voice=event.voice)
                        streams[key] = stream
                    entry = self._entry_for(measure, event, start)
                    if entry is not None:
                        stream.entries.append(entry)

        for stream in streams.values():
            stream.entries.sort(key=lambda entry: entry.absolute_onset)
        return tuple(streams.values())

    @staticmethod
    def _entry_for(measure: Measure, event: object, start: Fraction) -> StreamEntry | None:
        if isinstance(event, Note):
            return StreamEntry(
                measure=measure,
                absolute_onset=start + event.onset,
                duration=event.duration.quarter_length,
                note=event,
                tie_continuation=event.tie.stops and not event.tie.starts,
            )
        if isinstance(event, Chord):
            top = event.highest
            return StreamEntry(
                measure=measure,
                absolute_onset=start + event.onset,
                duration=event.duration.quarter_length,
                note=top,
                chord=event,
                tie_continuation=top.tie.stops and not top.tie.starts,
            )
        if isinstance(event, Rest):
            return StreamEntry(
                measure=measure,
                absolute_onset=start + event.onset,
                duration=event.duration.quarter_length,
                rest=event,
            )
        return None

    @cached_property
    def streams_by_key(self) -> dict[tuple[str, int, str], VoiceStream]:
        return {stream.key: stream for stream in self.streams}

    # -- harmony -------------------------------------------------------------------

    @cached_property
    def vertical_slices(self) -> tuple[VerticalSlice, ...]:
        """Simultaneities across all parts, sampled at every distinct onset.

        Built by a sweep rather than by sampling a grid, which keeps it O(n log n) and exact for
        any rhythm, including tuplets against straight notes.
        """
        refs = sorted(self.note_refs, key=lambda ref: ref.absolute_onset)
        if not refs:
            return ()
        onsets = sorted({ref.absolute_onset for ref in refs})
        slices: list[VerticalSlice] = []
        active: list[NoteRef] = []
        cursor = 0
        for onset in onsets:
            while cursor < len(refs) and refs[cursor].absolute_onset <= onset:
                active.append(refs[cursor])
                cursor += 1
            active = [ref for ref in active if ref.end > onset]
            if active:
                slices.append(VerticalSlice(onset=onset, notes=tuple(active)))
        return tuple(slices)

    # -- keys ----------------------------------------------------------------------

    @cached_property
    def global_key_estimate(self) -> KeyEstimate:
        profile = np.zeros(12, dtype=np.float64)
        for ref in self.note_refs:
            profile[ref.sounding.pitch_class] += float(ref.note.duration.quarter_length) or 0.25
        return estimate_key(
            profile, self.notated_key(self.score.parts[0]) if self.score.parts else None
        )

    @cached_property
    def _key_windows(self) -> dict[str, list[KeyEstimate]]:
        """Local key estimate per part, indexed by measure.

        Estimated from the whole score's texture rather than one part at a time would be more
        accurate harmonically, but a single wrong part would then poison every other part's
        context. Per-part keeps errors local, which is the right trade for a proofreader.
        """
        width = self.config.key_window_measures
        result: dict[str, list[KeyEstimate]] = {}
        for part in self.score.parts:
            profiles: list[np.ndarray] = [np.zeros(12) for _ in part.measures]
            for index in range(len(part.measures)):
                for ref in self.notes_by_measure.get((part.id, index), ()):
                    profiles[index][ref.sounding.pitch_class] += (
                        float(ref.note.duration.quarter_length) or 0.25
                    )
            estimates: list[KeyEstimate] = []
            for index in range(len(part.measures)):
                low = max(0, index - width // 2)
                high = min(len(profiles), low + width)
                window = np.sum(profiles[low:high], axis=0) if profiles[low:high] else np.zeros(12)
                notated = self.notated_key_at(part, index)
                estimates.append(estimate_key(window, notated))
            result[part.id] = estimates
        return result

    def local_key(self, part: Part, measure_index: int) -> KeyEstimate:
        estimates = self._key_windows.get(part.id, [])
        if 0 <= measure_index < len(estimates):
            return estimates[measure_index]
        return self.global_key_estimate

    def notated_key(self, part: Part) -> KeySignature | None:
        for measure in part.measures:
            return measure.attributes.key
        return None

    def notated_key_at(self, part: Part, measure_index: int) -> KeySignature | None:
        measure = part.measure_by_index(measure_index)
        return measure.attributes.key if measure else self.notated_key(part)

    # -- accidentals ---------------------------------------------------------------

    def accidental_state(self, measure: Measure, staff: int) -> AccidentalState:
        """A fresh accidental tracker for one measure and staff."""
        return AccidentalState(key=measure.attributes.key_for_staff(staff))

    # -- attributes ----------------------------------------------------------------

    @staticmethod
    def clef_at(measure: Measure, staff: int, onset: Fraction) -> Clef:
        """Clef in force at a point inside a measure, honouring mid-measure clef changes."""
        clef = measure.attributes.clef_for_staff(staff)
        for change in measure.attribute_changes:
            if change.onset <= onset and staff in change.clefs:
                clef = change.clefs[staff]
        return clef

    @staticmethod
    def time_at(measure: Measure) -> TimeSignature | None:
        return measure.attributes.time

    # -- convenience ---------------------------------------------------------------

    @property
    def note_count(self) -> int:
        return len(self.note_refs)

    def notes_in(self, part_id: str, measure_index: int) -> Sequence[NoteRef]:
        return self.notes_by_measure.get((part_id, measure_index), ())
