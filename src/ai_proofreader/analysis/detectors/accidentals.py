"""Accidental detectors.

Three different questions, deliberately kept as three rules:

1. **Is the file self-consistent?** Notation says an accidental holds for the rest of the bar at
   that octave. A file whose ``<alter>`` disagrees with what its own printed accidentals imply is
   wrong regardless of what the page says. Near-zero false positives.
2. **Did a natural go missing?** A note inheriting an accidental from several beats earlier, in a
   passage whose key says it should be natural, is the classic missed-courtesy-natural. Real, but
   genuinely ambiguous without the scan — so it is scored as a question, not an answer.
3. **Is this chromatic note isolated?** A single foreign pitch in an otherwise clearly diatonic
   passage, appearing nowhere else nearby, is more likely a misread accidental than a modulation.
   The noisiest of the three, and gated hardest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from fractions import Fraction

from ...models import (
    ALTER_ACCIDENTAL,
    Accidental,
    Channel,
    Note,
    SetPitchOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ...music_theory import AccidentalState, expected_alter
from ..base import Detector, make_suggestion
from ..context import AnalysisContext, NoteRef

__all__ = [
    "AccidentalConsistencyDetector",
    "CarriedAccidentalDetector",
    "ChromaticOutlierDetector",
]


def _staff_notes(
    context: AnalysisContext, part_id: str, measure_index: int, staff: int
) -> list[Note]:
    """Notes on one staff of one measure, in sounding order across all voices."""
    notes = [
        ref.note
        for ref in context.notes_in(part_id, measure_index)
        if ref.note.staff == staff and not ref.note.is_grace
    ]
    notes.sort(key=lambda note: (note.onset, note.pitch.diatonic))
    return notes


class AccidentalConsistencyDetector(Detector):
    """Notes whose sounding alteration contradicts the accidentals printed in their own bar."""

    name = "accidental_consistency"
    description = "Sounding pitch disagrees with the accidentals engraved in the same measure"
    kinds = (SuggestionKind.ACCIDENTAL_CHANGE, SuggestionKind.PITCH)
    channel = Channel.MUSICAL
    defaults = {"score": 0.78}

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part in context.score.parts:
            for measure in part.measures:
                staves = {event.staff for event in measure.events}
                for staff in sorted(staves):
                    state = AccidentalState(key=measure.attributes.key_for_staff(staff))
                    for note in _staff_notes(context, part.id, measure.index, staff):
                        implied = expected_alter(state, note)
                        state.observe(staff, note.pitch, note.accidental)
                        if note.pitch.alter == implied:
                            continue
                        if note.tie.stops:
                            continue  # a tied note keeps its accidental across the barline
                        yield self._report(part, measure, note, implied, staff)

    def _report(
        self, part: object, measure: object, note: Note, implied: int, staff: int
    ) -> Suggestion:
        implied_pitch = note.pitch.with_alter(implied)
        printed = (
            f"a printed {note.accidental.value}"
            if note.accidental
            else "the accidentals already in force in this bar"
        )
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.PITCH,
            severity=Severity.HIGH,
            part=part,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            onset=note.onset,
            staff=staff,
            voice=note.voice,
            refs=(note.ref,),
            title=f"{note.pitch.name} contradicts this bar's accidentals",
            explanation=(
                f"The file records this note as {note.pitch.name}, but {printed} make it "
                f"{implied_pitch.name} as engraved. A reader looking at the page would play "
                f"{implied_pitch.name}. Either the recognized pitch is wrong, or an accidental "
                "on this note was missed."
            ),
            current_repr=note.pitch.name,
            suggested_repr=implied_pitch.name,
            evidence=(
                self.evidence(
                    self.param("score"),
                    f"engraving implies {implied_pitch.name}, file says {note.pitch.name}",
                    implied=implied,
                    actual=note.pitch.alter,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=note.pitch.step,
                    alter=implied,
                    octave=note.pitch.octave,
                    accidental=None,
                    set_accidental=False,
                ),
            ),
        )


class CarriedAccidentalDetector(Detector):
    """Notes whose alteration is inherited from far earlier in the bar, where the key says
    otherwise.

    Engravers usually print a courtesy natural in this situation, so its absence is suspicious —
    but only suspicious. The scan settles it, which is why this rule's ceiling is low and its
    explanation says so out loud.
    """

    name = "carried_accidental"
    description = "Long-range accidental carry-over where the key implies a natural"
    kinds = (SuggestionKind.ACCIDENTAL_ADD, SuggestionKind.PITCH)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.45,
        #: How far, in quarter notes, the note must be from the accidental it inherited.
        "min_distance": 2.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        minimum = Fraction(self.param("min_distance")).limit_denominator(16)
        for part in context.score.parts:
            for measure in part.measures:
                local = context.local_key(part, measure.index)
                if not local.is_reliable:
                    continue
                diatonic = local.pitch_classes()
                for staff in sorted({event.staff for event in measure.events}):
                    key = measure.attributes.key_for_staff(staff)
                    state = AccidentalState(key=key)
                    set_at: dict[tuple[int, object, int], Fraction] = {}
                    for note in _staff_notes(context, part.id, measure.index, staff):
                        signature = (staff, note.pitch.step, note.pitch.octave)
                        inherited_from = set_at.get(signature)
                        if note.accidental is not None:
                            state.observe(staff, note.pitch, note.accidental)
                            set_at[signature] = note.onset
                            continue

                        key_alter = key.alter_for(note.pitch.step)
                        if note.pitch.alter == key_alter or inherited_from is None:
                            continue
                        if note.onset - inherited_from < minimum:
                            continue

                        natural = note.pitch.with_alter(key_alter)
                        sounding = part.transpose.apply(note.pitch)
                        natural_sounding = part.transpose.apply(natural)
                        if sounding.pitch_class in diatonic:
                            continue
                        if natural_sounding.pitch_class not in diatonic:
                            continue

                        yield self._report(part, measure, note, natural, staff, local.name)

    def _report(
        self,
        part: object,
        measure: object,
        note: Note,
        natural: object,
        staff: int,
        key_name: str,
    ) -> Suggestion:
        accidental = ALTER_ACCIDENTAL.get(natural.alter, Accidental.NATURAL)  # type: ignore[union-attr]
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.ACCIDENTAL_ADD,
            severity=Severity.MEDIUM,
            part=part,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            onset=note.onset,
            staff=staff,
            voice=note.voice,
            refs=(note.ref,),
            title=f"{note.pitch.name} may be missing a {accidental.value}",
            explanation=(
                f"This note inherits its accidental from earlier in the bar and lands outside "
                f"{key_name}, while {natural.name} would fit. Engravers normally print a "  # type: ignore[union-attr]
                "courtesy accidental here, so one may have been missed during recognition. "
                "Worth checking against the page — this reading cannot be settled from the "
                "notation alone."
            ),
            current_repr=note.pitch.name,
            suggested_repr=natural.name,  # type: ignore[union-attr]
            evidence=(
                self.evidence(
                    self.param("score"),
                    f"inherited accidental leaves the note outside {key_name}",
                    key=key_name,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=note.pitch.step,
                    alter=natural.alter,  # type: ignore[union-attr]
                    octave=note.pitch.octave,
                    accidental=accidental,
                    set_accidental=True,
                ),
            ),
        )


class ChromaticOutlierDetector(Detector):
    """A lone foreign pitch in a passage that is otherwise firmly in one key."""

    name = "chromatic_outlier"
    description = "Isolated non-diatonic notes in a clearly established key"
    kinds = (SuggestionKind.ACCIDENTAL_REMOVE, SuggestionKind.ACCIDENTAL_CHANGE)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.5,
        #: Measures either side of the note used to judge isolation.
        "window": 2.0,
        #: How many other occurrences of the same pitch class are tolerated in that window.
        "max_neighbours": 1.0,
        "min_key_correlation": 0.7,
        "min_key_clarity": 0.1,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        window = int(self.param("window"))
        max_neighbours = int(self.param("max_neighbours"))

        for part in context.score.parts:
            counts_by_measure = self._pitch_class_counts(context, part.id)
            for measure in part.measures:
                local = context.local_key(part, measure.index)
                if (
                    local.correlation < self.param("min_key_correlation")
                    or local.clarity < self.param("min_key_clarity")
                    or not local.is_reliable
                ):
                    continue
                diatonic = local.pitch_classes()
                key = measure.attributes.key

                for ref in context.notes_in(part.id, measure.index):
                    note = ref.note
                    if note.is_grace or ref.sounding.pitch_class in diatonic:
                        continue
                    key_alter = key.alter_for(note.pitch.step)
                    if note.pitch.alter == key_alter:
                        continue  # the signature itself put it outside the local key; not our call
                    repaired = note.pitch.with_alter(key_alter)
                    if part.transpose.apply(repaired).pitch_class not in diatonic:
                        continue

                    neighbours = self._neighbour_count(
                        counts_by_measure, measure.index, window, ref.sounding.pitch_class
                    )
                    if neighbours - 1 > max_neighbours:
                        continue

                    yield self._report(
                        part, measure, ref, repaired, local.name, neighbours - 1, key_alter
                    )

    @staticmethod
    def _pitch_class_counts(context: AnalysisContext, part_id: str) -> dict[int, Counter[int]]:
        counts: dict[int, Counter[int]] = {}
        for (other_part, index), refs in context.notes_by_measure.items():
            if other_part != part_id:
                continue
            bucket = counts.setdefault(index, Counter())
            for ref in refs:
                bucket[ref.sounding.pitch_class] += 1
        return counts

    @staticmethod
    def _neighbour_count(
        counts: dict[int, Counter[int]], index: int, window: int, pitch_class: int
    ) -> int:
        return sum(
            counts.get(other, Counter()).get(pitch_class, 0)
            for other in range(index - window, index + window + 1)
        )

    def _report(
        self,
        part: object,
        measure: object,
        ref: NoteRef,
        repaired: object,
        key_name: str,
        neighbours: int,
        key_alter: int,
    ) -> Suggestion:
        note = ref.note
        removing = note.accidental is not None
        kind = SuggestionKind.ACCIDENTAL_REMOVE if removing else SuggestionKind.ACCIDENTAL_CHANGE
        accidental = ALTER_ACCIDENTAL.get(key_alter, Accidental.NATURAL)
        isolation = (
            "it appears nowhere else nearby"
            if neighbours == 0
            else f"it appears only {neighbours} other time(s) nearby"
        )
        return make_suggestion(
            detector=self.name,
            kind=kind,
            severity=Severity.MEDIUM,
            part=part,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            onset=note.onset,
            staff=note.staff,
            voice=note.voice,
            refs=(note.ref,),
            title=f"{note.pitch.name} is the only chromatic note here",
            explanation=(
                f"The surrounding music sits firmly in {key_name} and {isolation}. "
                f"{repaired.name} would be the diatonic reading. "  # type: ignore[union-attr]
                + (
                    "A stray accidental is a common recognition artefact."
                    if removing
                    else "The recognized alteration does not come from a printed accidental."
                )
            ),
            current_repr=note.pitch.name,
            suggested_repr=repaired.name,  # type: ignore[union-attr]
            evidence=(
                self.evidence(
                    self.param("score"),
                    f"non-diatonic in {key_name}; {isolation}",
                    key=key_name,
                    neighbours=neighbours,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=note.pitch.step,
                    alter=key_alter,
                    octave=note.pitch.octave,
                    accidental=None if removing else accidental,
                    set_accidental=True,
                ),
            ),
        )
