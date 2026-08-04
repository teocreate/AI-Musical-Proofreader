"""Motif indexing and approximate matching.

The single most reliable signal available without looking at the scan is *self-similarity*.
Music repeats itself, OMR errors do not. When a four-note figure appears in bars 8, 16 and 24 and
the bar-16 copy differs from the other two by one semitone in one position, the odds that the
composer wrote it that way are far lower than the odds that a sharp was missed.

Two representations make this work:

* **Diatonic shape** — staff-position offsets from the first note of the window. A misread
  notehead changes exactly one entry.
* **Chromatic shape** — semitone offsets from the first note. A missed accidental changes exactly
  one entry while leaving the diatonic shape identical.

Grouping by shape gives transposition invariance for free — a motif answered a fifth higher is
still the same motif — and comparing the two shapes separates "wrong notehead" from "wrong
accidental" without any further reasoning. Rhythm is part of the grouping key because a pitch
sequence that recurs with a different rhythm is usually a coincidence, and coincidences are what
destroy precision.

Complexity is O(n · L) in notes and window length: each window contributes L masked keys, and
every lookup is a dict hit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from ...models import Pitch
from ..context import StreamEntry, VoiceStream

__all__ = ["MotifDeviation", "MotifIndex", "MotifOccurrence", "transpose_pitch"]


def _difference_count(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """How many positions two shapes disagree on."""
    return sum(1 for a, b in zip(left, right, strict=False) if a != b)


def _independent(odd: MotifOccurrence, candidates: list[MotifOccurrence]) -> list[MotifOccurrence]:
    """Drop occurrences that share notes with ``odd``.

    Windows slide by one note, so a figure of length L produces L windows containing any given
    note. Counting those as separate corroborating occurrences would manufacture support out of
    nothing — the same handful of notes, counted repeatedly.
    """
    occupied = {id(entry) for entry in odd.entries}
    return [
        occurrence
        for occurrence in candidates
        if not occupied.intersection(id(entry) for entry in occurrence.entries)
    ]


def transpose_pitch(pitch: Pitch, diatonic_delta: int, chromatic_delta: int) -> Pitch:
    """Move ``pitch`` by a spelled interval, preserving letter-name logic.

    Both deltas are needed to keep the spelling right: moving a G-sharp up by a minor third
    should give a B, not a C-flat.
    """
    target_diatonic = pitch.diatonic + diatonic_delta
    target_midi = pitch.midi + chromatic_delta
    candidate = Pitch.from_diatonic(target_diatonic, alter=0)
    return candidate.with_alter(max(-4, min(4, target_midi - candidate.midi)))


@dataclass(frozen=True)
class MotifOccurrence:
    """One window of consecutive melodic notes, described relative to its first note."""

    stream_key: tuple[str, int, str]
    entries: tuple[StreamEntry, ...]
    diatonic_shape: tuple[int, ...]
    chromatic_shape: tuple[int, ...]
    rhythm: tuple[Fraction, ...]

    @property
    def first_pitch(self) -> Pitch:
        assert self.entries[0].note is not None
        return self.entries[0].note.pitch

    @property
    def start_measure(self) -> int:
        return self.entries[0].measure.index

    def note_at(self, position: int) -> StreamEntry:
        return self.entries[position]

    def label(self) -> str:
        return f"m.{self.entries[0].measure.number}"


@dataclass(frozen=True)
class MotifDeviation:
    """One occurrence disagreeing with its siblings at exactly one position."""

    occurrence: MotifOccurrence
    position: int
    #: The occurrences that agree with each other and disagree with this one.
    agreeing: tuple[MotifOccurrence, ...]
    kind: Literal["chromatic", "diatonic"]
    #: What the note should be, computed by transposing the majority reading into this context.
    expected_pitch: Pitch
    #: Semitone (chromatic) or staff-position (diatonic) size of the disagreement.
    delta: int

    @property
    def support(self) -> int:
        return len(self.agreeing)

    @property
    def entry(self) -> StreamEntry:
        return self.occurrence.entries[self.position]

    def describe(self) -> str:
        bars = ", ".join(occurrence.label() for occurrence in self.agreeing[:4])
        return (
            f"the same figure appears at {bars} with "
            f"{self.expected_pitch.name} in this position"
        )


class MotifIndex:
    """Windowed motif index over a set of voice streams."""

    def __init__(self, length: int = 4, min_occurrences: int = 3) -> None:
        self.length = max(2, length)
        self.min_occurrences = max(2, min_occurrences)
        self.occurrences: list[MotifOccurrence] = []
        self._exact: dict[tuple[object, ...], list[int]] = defaultdict(list)
        self._masked: dict[tuple[object, ...], list[int]] = defaultdict(list)

    # -- building ------------------------------------------------------------------

    def add_stream(self, stream: VoiceStream) -> None:
        """Index every window of melodic notes in one voice."""
        for run in stream.melodic_runs(min_length=self.length):
            self._add_run(stream.key, run)

    def _add_run(self, stream_key: tuple[str, int, str], run: list[StreamEntry]) -> None:
        length = self.length
        for start in range(len(run) - length + 1):
            window = run[start : start + length]
            first = window[0].note
            if first is None:
                continue
            base_diatonic = first.pitch.diatonic
            base_midi = first.pitch.midi
            diatonic_shape: list[int] = []
            chromatic_shape: list[int] = []
            rhythm: list[Fraction] = []
            usable = True
            for entry in window:
                if entry.note is None:
                    usable = False
                    break
                diatonic_shape.append(entry.note.pitch.diatonic - base_diatonic)
                chromatic_shape.append(entry.note.pitch.midi - base_midi)
                rhythm.append(entry.duration)
            if not usable:
                continue

            occurrence = MotifOccurrence(
                stream_key=stream_key,
                entries=tuple(window),
                diatonic_shape=tuple(diatonic_shape),
                chromatic_shape=tuple(chromatic_shape),
                rhythm=tuple(rhythm),
            )
            index = len(self.occurrences)
            self.occurrences.append(occurrence)

            rhythm_key = occurrence.rhythm
            # Exact bucket: identical staff positions and rhythm. Members can still differ in
            # accidentals, which is precisely what pass A looks for.
            self._exact[(rhythm_key, occurrence.diatonic_shape)].append(index)
            # Masked buckets: identical everywhere but one position, for pass B.
            for position in range(1, length):
                shape = occurrence.diatonic_shape
                masked = (*shape[:position], None, *shape[position + 1 :])
                self._masked[(rhythm_key, masked, position)].append(index)

    # -- querying ------------------------------------------------------------------

    def deviations(
        self, max_chromatic_delta: int = 2, max_diatonic_delta: int = 1
    ) -> list[MotifDeviation]:
        """Find single-position disagreements in both representations.

        Only *isolated* disagreements count: one occurrence against a clear majority. Two
        occurrences differing from two others is a variant, not an error, and we say nothing.

        The two limits differ on purpose. A misread notehead lands on the *adjacent* line or
        space, so a diatonic disagreement of one step is the error signature and a disagreement
        of a third is far more likely to be a real compositional variant. Accidentals have more
        room — a sharp missed on an already-flat note moves the pitch two semitones — so the
        chromatic limit is looser.
        """
        results: list[MotifDeviation] = []
        results.extend(self._chromatic_deviations(max_chromatic_delta))
        results.extend(self._diatonic_deviations(max_diatonic_delta))
        return results

    def _chromatic_deviations(self, max_delta: int) -> list[MotifDeviation]:
        """Same staff positions and rhythm, one note altered differently."""
        found: list[MotifDeviation] = []
        for indices in self._exact.values():
            if len(indices) < self.min_occurrences:
                continue
            group = [self.occurrences[index] for index in indices]
            length = self.length
            for position in range(length):
                values: dict[int, list[MotifOccurrence]] = defaultdict(list)
                for occurrence in group:
                    values[occurrence.chromatic_shape[position]].append(occurrence)
                if len(values) != 2:
                    continue
                ordered = sorted(values.items(), key=lambda item: len(item[1]), reverse=True)
                (majority_value, majority), (minority_value, minority) = ordered
                if len(minority) != 1:
                    continue
                majority = _independent(minority[0], majority)
                if len(majority) < self.min_occurrences - 1:
                    continue
                delta = minority_value - majority_value
                if not 0 < abs(delta) <= max_delta:
                    continue
                odd = minority[0]
                reference = majority[0]
                # A window whose *first* note carries the error has every later offset shifted,
                # which would otherwise produce one bogus finding per position. Require the
                # disagreement to be genuinely isolated; the window that starts one note later
                # will catch the real culprit.
                if _difference_count(odd.chromatic_shape, reference.chromatic_shape) != 1:
                    continue
                expected = self._expected_pitch(odd, reference, position)
                if expected is None:
                    continue
                found.append(
                    MotifDeviation(
                        occurrence=odd,
                        position=position,
                        agreeing=tuple(majority),
                        kind="chromatic",
                        expected_pitch=expected,
                        delta=delta,
                    )
                )
        return found

    def _diatonic_deviations(self, max_delta: int) -> list[MotifDeviation]:
        """Same rhythm and same shape except one staff position."""
        found: list[MotifDeviation] = []
        for (_, _, position), indices in self._masked.items():
            if len(indices) < self.min_occurrences:
                continue
            group = [self.occurrences[index] for index in indices]
            values: dict[int, list[MotifOccurrence]] = defaultdict(list)
            for occurrence in group:
                values[occurrence.diatonic_shape[position]].append(occurrence)
            if len(values) != 2:
                continue
            ordered = sorted(values.items(), key=lambda item: len(item[1]), reverse=True)
            (majority_value, majority), (minority_value, minority) = ordered
            if len(minority) != 1:
                continue
            majority = _independent(minority[0], majority)
            if len(majority) < self.min_occurrences - 1:
                continue
            delta = minority_value - majority_value
            if not 0 < abs(delta) <= max_delta:
                continue
            odd = minority[0]
            reference = majority[0]
            if _difference_count(odd.diatonic_shape, reference.diatonic_shape) != 1:
                continue
            expected = self._expected_pitch(odd, reference, position)
            if expected is None:
                continue
            found.append(
                MotifDeviation(
                    occurrence=odd,
                    position=position,
                    agreeing=tuple(majority),
                    kind="diatonic",
                    expected_pitch=expected,
                    delta=delta,
                )
            )
        return found

    @staticmethod
    def _expected_pitch(
        odd: MotifOccurrence, reference: MotifOccurrence, position: int
    ) -> Pitch | None:
        """Transpose the majority reading into the odd occurrence's register."""
        reference_note = reference.entries[position].note
        if reference_note is None:
            return None
        diatonic_delta = odd.first_pitch.diatonic - reference.first_pitch.diatonic
        chromatic_delta = odd.first_pitch.midi - reference.first_pitch.midi
        return transpose_pitch(reference_note.pitch, diatonic_delta, chromatic_delta)

    # -- statistics ----------------------------------------------------------------

    @property
    def repeated_motif_count(self) -> int:
        return sum(1 for indices in self._exact.values() if len(indices) >= self.min_occurrences)
