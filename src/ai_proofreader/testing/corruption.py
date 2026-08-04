"""Error injection: turning a clean score into a realistically mis-recognized one.

A precision claim without a labelled corpus is marketing. This module builds the corpus by taking
scores we know to be correct and introducing the mistakes an OMR engine actually makes, recording
exactly what was changed so the evaluation harness can score the detectors against ground truth.

**Realism is the whole point.** Injecting a "missing sharp" by editing ``<alter>`` and leaving the
``<accidental>`` element behind would produce a file MuseScore would never write, and the
consistency detector would catch it instantly — flattering the numbers and teaching us nothing.
So every injection here mimics what MuseScore exports when its recognizer got that particular
thing wrong: a missed sharp removes the accidental *and* changes the sounding alter to what the
key signature implies, exactly as a note that was never seen to be sharp would be written.

Injections are applied through :class:`~ai_proofreader.edits.EditApplier`, which means the corpus
generator exercises the same code path that applies corrections. A bug in the applier shows up as
a broken corpus rather than as a silent wrong answer in production.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from pathlib import Path

from ..edits import EditApplier
from ..models import (
    ALTER_ACCIDENTAL,
    Chord,
    Note,
    Part,
    RemoveElementOp,
    Rest,
    Score,
    SetClefOp,
    SetDurationOp,
    SetPitchOp,
    SetTieOp,
    SuggestionKind,
)
from ..music_theory.rhythm import try_duration_to_type
from ..score_parser import SourceDocument, parse_score

__all__ = ["CorruptionResult", "Corruptor", "ErrorKind", "InjectedError", "corrupt_file"]


class ErrorKind(StrEnum):
    """The OMR failure modes we reproduce."""

    NOTEHEAD_SHIFT = "notehead_shift"
    MISSING_ACCIDENTAL = "missing_accidental"
    SPURIOUS_ACCIDENTAL = "spurious_accidental"
    MISSING_DOT = "missing_dot"
    HALVED_DURATION = "halved_duration"
    MISSING_TIE = "missing_tie"
    BROKEN_TIE = "broken_tie"
    DROPPED_REST = "dropped_rest"
    WRONG_CLEF = "wrong_clef"

    @property
    def expected_kinds(self) -> tuple[SuggestionKind, ...]:
        """Suggestion kinds that count as correctly identifying this error."""
        return _EXPECTED[self]


_EXPECTED: dict[ErrorKind, tuple[SuggestionKind, ...]] = {
    ErrorKind.NOTEHEAD_SHIFT: (SuggestionKind.PITCH,),
    ErrorKind.MISSING_ACCIDENTAL: (
        SuggestionKind.PITCH,
        SuggestionKind.ACCIDENTAL_ADD,
        SuggestionKind.ACCIDENTAL_CHANGE,
    ),
    ErrorKind.SPURIOUS_ACCIDENTAL: (
        SuggestionKind.PITCH,
        SuggestionKind.ACCIDENTAL_REMOVE,
        SuggestionKind.ACCIDENTAL_CHANGE,
    ),
    ErrorKind.MISSING_DOT: (
        SuggestionKind.DOT_ADD,
        SuggestionKind.DURATION,
        SuggestionKind.REST,
    ),
    ErrorKind.HALVED_DURATION: (
        SuggestionKind.DURATION,
        SuggestionKind.DOT_ADD,
        SuggestionKind.REST,
    ),
    ErrorKind.MISSING_TIE: (SuggestionKind.TIE_ADD,),
    ErrorKind.BROKEN_TIE: (SuggestionKind.PITCH, SuggestionKind.TIE_REMOVE),
    ErrorKind.DROPPED_REST: (SuggestionKind.REST, SuggestionKind.DURATION),
    ErrorKind.WRONG_CLEF: (SuggestionKind.CLEF,),
}


@dataclass(frozen=True)
class InjectedError:
    """Ground truth for one injected mistake.

    Located in *musical* coordinates rather than by element handle: the corrupted file is
    re-parsed before analysis, and structural injections shift every handle after them.
    """

    kind: ErrorKind
    part_id: str
    measure_index: int
    onset: Fraction
    staff: int
    voice: str
    before: str
    after: str
    description: str

    @property
    def expected_kinds(self) -> tuple[SuggestionKind, ...]:
        return self.kind.expected_kinds

    def key(self) -> tuple[str, int, str]:
        return (self.part_id, self.measure_index, str(self.onset))

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "part_id": self.part_id,
            "measure_index": self.measure_index,
            "onset": str(self.onset),
            "staff": self.staff,
            "voice": self.voice,
            "before": self.before,
            "after": self.after,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> InjectedError:
        return cls(
            kind=ErrorKind(str(data["kind"])),
            part_id=str(data["part_id"]),
            measure_index=int(data["measure_index"]),  # type: ignore[arg-type]
            onset=Fraction(str(data["onset"])),
            staff=int(data["staff"]),  # type: ignore[arg-type]
            voice=str(data["voice"]),
            before=str(data["before"]),
            after=str(data["after"]),
            description=str(data["description"]),
        )


@dataclass
class CorruptionResult:
    document: SourceDocument
    errors: list[InjectedError] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.errors)


class Corruptor:
    """Injects OMR-style errors into a parsed score."""

    def __init__(self, document: SourceDocument, score: Score, seed: int = 0) -> None:
        self.document = document
        self.score = score
        self.rng = random.Random(seed)
        self.applier = EditApplier(document)
        self.errors: list[InjectedError] = []

    # -- driving -------------------------------------------------------------------

    def inject(self, kinds: Sequence[ErrorKind], count: int) -> CorruptionResult:
        """Attempt ``count`` injections drawn from ``kinds``.

        Injections that find no valid target are skipped rather than forced, so a corpus entry
        may end up with fewer errors than requested — the manifest records what was actually
        injected, never what was intended.
        """
        attempts = 0
        used: set[tuple[str, int, str]] = set()
        while len(self.errors) < count and attempts < count * 12:
            attempts += 1
            kind = self.rng.choice(list(kinds))
            error = self._inject_one(kind, used)
            if error is not None:
                used.add(error.key())
                self.errors.append(error)
        return CorruptionResult(document=self.document, errors=list(self.errors))

    def _inject_one(self, kind: ErrorKind, used: set[tuple[str, int, str]]) -> InjectedError | None:
        handler = {
            ErrorKind.NOTEHEAD_SHIFT: self._notehead_shift,
            ErrorKind.MISSING_ACCIDENTAL: self._missing_accidental,
            ErrorKind.SPURIOUS_ACCIDENTAL: self._spurious_accidental,
            ErrorKind.MISSING_DOT: self._missing_dot,
            ErrorKind.HALVED_DURATION: self._halved_duration,
            ErrorKind.MISSING_TIE: self._missing_tie,
            ErrorKind.BROKEN_TIE: self._broken_tie,
            ErrorKind.DROPPED_REST: self._dropped_rest,
            ErrorKind.WRONG_CLEF: self._wrong_clef,
        }[kind]
        return handler(used)

    # -- target selection ----------------------------------------------------------

    def _candidate_notes(self, used: set[tuple[str, int, str]]) -> list[tuple[Part, object, Note]]:
        candidates: list[tuple[Part, object, Note]] = []
        for part in self.score.parts:
            for measure in part.measures:
                for event in measure.events:
                    notes: tuple[Note, ...]
                    if isinstance(event, Note):
                        notes = (event,)
                    elif isinstance(event, Chord):
                        notes = event.notes
                    else:
                        continue
                    for note in notes:
                        if note.is_grace or note.ref < 0:
                            continue
                        if (part.id, measure.index, str(note.onset)) in used:
                            continue
                        candidates.append((part, measure, note))
        self.rng.shuffle(candidates)
        return candidates

    def _record(
        self,
        kind: ErrorKind,
        part: Part,
        measure: object,
        note: object,
        before: str,
        after: str,
        description: str,
    ) -> InjectedError:
        return InjectedError(
            kind=kind,
            part_id=part.id,
            measure_index=measure.index,  # type: ignore[attr-defined]
            onset=note.onset,  # type: ignore[attr-defined]
            staff=getattr(note, "staff", 1),
            voice=getattr(note, "voice", "1"),
            before=before,
            after=after,
            description=description,
        )

    # -- individual injections -----------------------------------------------------

    def _notehead_shift(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part, measure, note in self._candidate_notes(used):
            if note.tie.starts or note.tie.stops:
                continue  # a shifted tied note is a different error kind
            offset = self.rng.choice((-2, -1, 1, 2))
            key = measure.attributes.key_for_staff(note.staff)
            shifted = note.pitch.step_shifted(offset, alter=0)
            shifted = shifted.with_alter(key.alter_for(shifted.step))
            if abs(shifted.octave - note.pitch.octave) > 1:
                continue
            self.applier.apply(
                SetPitchOp(
                    ref=note.ref,
                    step=shifted.step,
                    alter=shifted.alter,
                    octave=shifted.octave,
                    accidental=None,
                    set_accidental=note.accidental is not None,
                )
            )
            return self._record(
                ErrorKind.NOTEHEAD_SHIFT,
                part,
                measure,
                note,
                note.pitch.name,
                shifted.name,
                f"notehead read {abs(offset)} staff position(s) "
                f"{'high' if offset > 0 else 'low'}",
            )
        return None

    def _missing_accidental(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        """Remove an accidental the way an engine that never saw the glyph would.

        Both the printed accidental and the sounding alteration go, the latter falling back to
        whatever the key signature implies — which is what MuseScore writes for a note it read as
        plain.
        """
        for part, measure, note in self._candidate_notes(used):
            if note.accidental is None:
                continue
            key = measure.attributes.key_for_staff(note.staff)
            fallback = key.alter_for(note.pitch.step)
            if fallback == note.pitch.alter:
                continue
            plain = note.pitch.with_alter(fallback)
            self.applier.apply(
                SetPitchOp(
                    ref=note.ref,
                    step=plain.step,
                    alter=plain.alter,
                    octave=plain.octave,
                    accidental=None,
                    set_accidental=True,
                )
            )
            return self._record(
                ErrorKind.MISSING_ACCIDENTAL,
                part,
                measure,
                note,
                note.pitch.name,
                plain.name,
                "accidental glyph not recognized",
            )
        return None

    def _spurious_accidental(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        """Invent an accidental, as happens when a smudge or an ornament is read as one."""
        for part, measure, note in self._candidate_notes(used):
            if note.accidental is not None:
                continue
            key = measure.attributes.key_for_staff(note.staff)
            if note.pitch.alter != key.alter_for(note.pitch.step):
                continue
            delta = self.rng.choice((-1, 1))
            altered = note.pitch.with_alter(note.pitch.alter + delta)
            if abs(altered.alter) > 1:
                continue
            self.applier.apply(
                SetPitchOp(
                    ref=note.ref,
                    step=altered.step,
                    alter=altered.alter,
                    octave=altered.octave,
                    accidental=ALTER_ACCIDENTAL.get(altered.alter),
                    set_accidental=True,
                )
            )
            return self._record(
                ErrorKind.SPURIOUS_ACCIDENTAL,
                part,
                measure,
                note,
                note.pitch.name,
                altered.name,
                "accidental recognized where none was printed",
            )
        return None

    def _missing_dot(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part, measure, note in self._candidate_notes(used):
            if note.duration.dots < 1 or note.duration.ticks <= 0:
                continue
            divisions = measure.attributes.divisions
            undotted = note.duration.quarter_length * Fraction(2, 3)
            if note.duration.dots != 1:
                continue
            ticks = undotted * divisions
            if ticks.denominator != 1:
                continue
            self.applier.apply(
                SetDurationOp(
                    ref=note.ref,
                    note_type=note.duration.note_type,
                    dots=0,
                    ticks=int(ticks),
                    quarter_length=undotted,
                )
            )
            return self._record(
                ErrorKind.MISSING_DOT,
                part,
                measure,
                note,
                note.duration.describe(),
                f"undotted {note.duration.note_type}",
                "augmentation dot not recognized",
            )
        return None

    def _halved_duration(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part, measure, note in self._candidate_notes(used):
            if note.duration.dots or note.duration.time_modification:
                continue
            halved = note.duration.quarter_length / 2
            guess = try_duration_to_type(halved, allow_tuplets=False)
            if guess is None:
                continue
            divisions = measure.attributes.divisions
            ticks = halved * divisions
            if ticks.denominator != 1:
                continue
            self.applier.apply(
                SetDurationOp(
                    ref=note.ref,
                    note_type=guess[0],
                    dots=0,
                    ticks=int(ticks),
                    quarter_length=halved,
                )
            )
            return self._record(
                ErrorKind.HALVED_DURATION,
                part,
                measure,
                note,
                note.duration.describe(),
                guess[0],
                "an extra flag was read on the stem",
            )
        return None

    def _missing_tie(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part, measure, note in self._candidate_notes(used):
            if not note.tie.stops:
                continue
            self.applier.apply(SetTieOp(ref=note.ref, stop=False))
            return self._record(
                ErrorKind.MISSING_TIE,
                part,
                measure,
                note,
                f"{note.pitch.name} (tied)",
                f"{note.pitch.name} (untied)",
                "the far end of a tie curve was lost",
            )
        return None

    def _broken_tie(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part, measure, note in self._candidate_notes(used):
            if not note.tie.stops:
                continue
            shifted = note.pitch.step_shifted(1, alter=0)
            self.applier.apply(
                SetPitchOp(
                    ref=note.ref,
                    step=shifted.step,
                    alter=shifted.alter,
                    octave=shifted.octave,
                    accidental=None,
                    set_accidental=False,
                )
            )
            return self._record(
                ErrorKind.BROKEN_TIE,
                part,
                measure,
                note,
                note.pitch.name,
                shifted.name,
                "the tied-to notehead was read one position off",
            )
        return None

    def _dropped_rest(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part in self.score.parts:
            for measure in part.measures:
                for event in measure.events:
                    if not isinstance(event, Rest) or event.ref < 0 or event.is_measure_rest:
                        continue
                    if (part.id, measure.index, str(event.onset)) in used:
                        continue
                    self.applier.apply(
                        RemoveElementOp(ref=event.ref, reason="simulated dropped rest glyph")
                    )
                    return self._record(
                        ErrorKind.DROPPED_REST,
                        part,
                        measure,
                        event,
                        event.describe(),
                        "(nothing)",
                        "a rest glyph was not recognized",
                    )
        return None

    def _wrong_clef(self, used: set[tuple[str, int, str]]) -> InjectedError | None:
        for part in self.score.parts:
            for measure in part.measures:
                clefs = measure.attributes.clefs
                for _staff, clef in clefs.items():
                    if clef.ref < 0:
                        continue
                    replacement = ("F", 4) if clef.sign.value == "G" else ("G", 2)
                    self.applier.apply(
                        SetClefOp(ref=clef.ref, sign=replacement[0], line=replacement[1])
                    )
                    return self._record(
                        ErrorKind.WRONG_CLEF,
                        part,
                        measure,
                        measure.events[0] if measure.events else measure,
                        clef.describe(),
                        f"{replacement[0]}{replacement[1]}",
                        "the clef glyph was misclassified",
                    )
        return None


def corrupt_file(
    source: str | Path,
    target: str | Path,
    kinds: Sequence[ErrorKind],
    count: int,
    seed: int = 0,
) -> list[InjectedError]:
    """Load a clean score, inject errors, and write the corrupted copy."""
    document = SourceDocument.load(source)
    score = parse_score(document)
    corruptor = Corruptor(document, score, seed=seed)
    result = corruptor.inject(kinds, count)
    result.document.save(target)
    return result.errors
