"""Tie and slur integrity.

Ties carry a hard constraint that makes them unusually informative: **a tie joins two notes of the
same pitch**. It is not a stylistic convention, it is what the symbol means. So a file containing
a tie from C4 to C-sharp4 is not merely odd — it is impossible, and one of those two notes is
misread. That single rule catches a whole class of missed-accidental errors that no amount of
harmonic reasoning would find, and it catches them with near-certainty.

Slurs have no such constraint, so slur checking is limited to structural integrity: brackets that
open and never close, or close having never opened.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...models import (
    ALTER_ACCIDENTAL,
    Channel,
    Chord,
    EditAction,
    Note,
    SetPitchOp,
    SetSlurOp,
    SetTieOp,
    Severity,
    SpannerRole,
    Suggestion,
    SuggestionKind,
)
from ..base import Detector, make_suggestion
from ..context import AnalysisContext, VoiceStream

__all__ = ["SlurStructureDetector", "TieIntegrityDetector"]


def _notes_of(entry: object) -> tuple[Note, ...]:
    """Every sounding note in a stream entry — chord members included."""
    chord = getattr(entry, "chord", None)
    if isinstance(chord, Chord):
        return chord.notes
    note = getattr(entry, "note", None)
    return (note,) if isinstance(note, Note) else ()


class TieIntegrityDetector(Detector):
    """Ties that dangle, or that join notes which are not the same pitch."""

    name = "tie_integrity"
    description = "Dangling ties, and ties between notes of different pitch"
    kinds = (SuggestionKind.TIE_ADD, SuggestionKind.TIE_REMOVE, SuggestionKind.PITCH)
    channel = Channel.MUSICAL
    defaults = {
        "pitch_mismatch_score": 0.9,
        "alter_mismatch_score": 0.92,
        "dangling_start_score": 0.72,
        "dangling_stop_score": 0.7,
        "notation_mismatch_score": 0.4,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for stream in context.streams:
            yield from self._check_stream(stream)

    def _check_stream(self, stream: VoiceStream) -> Iterable[Suggestion]:
        entries = stream.entries
        for index, entry in enumerate(entries):
            starts = [note for note in _notes_of(entry) if note.tie.starts]
            if not starts:
                continue
            following = entries[index + 1] if index + 1 < len(entries) else None
            if following is None:
                yield from self._dangling(stream, entry, starts, reason="end of part")
                continue
            if following.is_rest:
                yield from self._dangling(stream, entry, starts, reason="a rest follows")
                continue

            next_notes = _notes_of(following)
            stops = [note for note in next_notes if note.tie.stops]
            unmatched: list[Note] = []
            for note in starts:
                exact = next(
                    (other for other in stops if other.pitch.midi == note.pitch.midi), None
                )
                if exact is not None:
                    continue
                same_position = next(
                    (other for other in stops if other.pitch.diatonic == note.pitch.diatonic), None
                )
                if same_position is not None:
                    yield from self._alter_mismatch(stream, entry, note, following, same_position)
                    continue
                if stops:
                    yield from self._pitch_mismatch(stream, entry, note, following, stops[0])
                    continue
                same_pitch_present = next(
                    (other for other in next_notes if other.pitch.midi == note.pitch.midi), None
                )
                if same_pitch_present is not None:
                    yield from self._missing_stop(
                        stream, entry, note, following, same_pitch_present
                    )
                    continue
                unmatched.append(note)
            if unmatched:
                yield from self._dangling(
                    stream, entry, unmatched, reason="the next note is a different pitch"
                )

    # -- individual findings -------------------------------------------------------

    def _alter_mismatch(
        self, stream: VoiceStream, entry: object, note: Note, following: object, other: Note
    ) -> Iterable[Suggestion]:
        """A tie between the same staff position spelled with different alterations.

        This is the money case. The two notes sit on the same line, so no notehead was misread;
        the accidental on one of them is wrong. Which one is decided by who carries a printed
        accidental — the note *without* one is the one that inherited a value, and inherited
        values are what OMR gets wrong.
        """
        if note.accidental is not None and other.accidental is None:
            target, source = other, note
            target_entry = following
        else:
            target, source = note, other
            target_entry = entry

        new_alter = source.pitch.alter
        accidental = ALTER_ACCIDENTAL.get(new_alter)
        measure = target_entry.measure  # type: ignore[union-attr]
        yield make_suggestion(
            detector=self.name,
            kind=SuggestionKind.PITCH,
            severity=Severity.HIGH,
            part=stream.part,
            measure=measure,
            onset=target.onset,
            staff=target.staff,
            voice=target.voice,
            refs=(target.ref, source.ref),
            title=f"Tie joins {note.pitch.name} to {other.pitch.name}",
            explanation=(
                f"A tie can only join two notes of the same pitch, and these differ by "
                f"{abs(note.pitch.midi - other.pitch.midi)} semitone(s) on the same line. "
                f"{source.pitch.name} carries the printed accidental, so {target.pitch.name} is "
                f"the misread one and should be {source.pitch.name}."
            ),
            current_repr=target.pitch.name,
            suggested_repr=source.pitch.name,
            evidence=(
                self.evidence(
                    self.param("alter_mismatch_score"),
                    f"tie between {note.pitch.name} and {other.pitch.name}: same staff position, "
                    "different accidental",
                    from_pitch=note.pitch.name,
                    to_pitch=other.pitch.name,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=target.ref,
                    step=source.pitch.step,
                    alter=new_alter,
                    octave=target.pitch.octave,
                    accidental=accidental if target.accidental is not None else None,
                    set_accidental=target.accidental is not None,
                ),
            ),
        )

    def _pitch_mismatch(
        self, stream: VoiceStream, entry: object, note: Note, following: object, other: Note
    ) -> Iterable[Suggestion]:
        """A tie between two genuinely different pitches — one notehead is misplaced."""
        measure = following.measure  # type: ignore[union-attr]
        steps = abs(note.pitch.diatonic - other.pitch.diatonic)
        yield make_suggestion(
            detector=self.name,
            kind=SuggestionKind.PITCH,
            severity=Severity.HIGH,
            part=stream.part,
            measure=measure,
            onset=other.onset,
            staff=other.staff,
            voice=other.voice,
            refs=(other.ref, note.ref),
            title=f"Tie joins {note.pitch.name} to {other.pitch.name}",
            explanation=(
                f"A tie must join two notes of the same pitch, but these are {steps} staff "
                "position(s) apart — one of the two noteheads was read on the wrong line. The "
                "tied-to note is proposed as the correction; check the first note if the page "
                "says otherwise."
            ),
            current_repr=other.pitch.name,
            suggested_repr=note.pitch.name,
            evidence=(
                self.evidence(
                    self.param("pitch_mismatch_score"),
                    f"tie between {note.pitch.name} and {other.pitch.name}, which notation "
                    "does not allow",
                    steps=steps,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=other.ref,
                    step=note.pitch.step,
                    alter=note.pitch.alter,
                    octave=note.pitch.octave,
                    accidental=None,
                    set_accidental=False,
                ),
            ),
        )

    def _missing_stop(
        self, stream: VoiceStream, entry: object, note: Note, following: object, target: Note
    ) -> Iterable[Suggestion]:
        """A tie starts, and the next event does contain that pitch but is not marked as tied."""
        measure = following.measure  # type: ignore[union-attr]
        yield make_suggestion(
            detector=self.name,
            kind=SuggestionKind.TIE_ADD,
            severity=Severity.MEDIUM,
            part=stream.part,
            measure=measure,
            onset=target.onset,
            staff=target.staff,
            voice=target.voice,
            refs=(target.ref, note.ref),
            title=f"Tie on {note.pitch.name} is not closed",
            explanation=(
                f"A tie starts on {note.pitch.name} and the next event contains the same pitch, "
                "but it is not marked as the end of the tie. The far end of the curve was "
                "probably lost — this happens most often when a tie crosses a system break."
            ),
            current_repr=f"{target.pitch.name} (untied)",
            suggested_repr=f"{target.pitch.name} (tied)",
            evidence=(
                self.evidence(
                    self.param("dangling_start_score"),
                    f"tie starts on {note.pitch.name} with the same pitch immediately following",
                ),
            ),
            edits=(SetTieOp(ref=target.ref, stop=True),),
        )

    def _dangling(
        self, stream: VoiceStream, entry: object, notes: Sequence[Note], reason: str
    ) -> Iterable[Suggestion]:
        measure = entry.measure  # type: ignore[union-attr]
        for note in notes:
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.TIE_REMOVE,
                severity=Severity.MEDIUM,
                part=stream.part,
                measure=measure,
                onset=note.onset,
                staff=note.staff,
                voice=note.voice,
                refs=(note.ref,),
                title=f"Tie on {note.pitch.name} goes nowhere",
                explanation=(
                    f"This note starts a tie but nothing continues it — {reason}. Either the "
                    "curve was a slur that OMR read as a tie, or the note it joins to was lost."
                ),
                current_repr=f"{note.pitch.name} (tie start)",
                suggested_repr=f"{note.pitch.name} (no tie)",
                evidence=(
                    self.evidence(
                        self.param("dangling_stop_score"),
                        f"tie starts on {note.pitch.name} but {reason}",
                    ),
                ),
                edits=(SetTieOp(ref=note.ref, start=False),),
            )


class SlurStructureDetector(Detector):
    """Slurs that open without closing, or close without opening."""

    name = "slur_structure"
    description = "Unbalanced slur brackets within a part"
    kinds = (SuggestionKind.SLUR_ADD, SuggestionKind.SLUR_REMOVE)
    channel = Channel.MUSICAL
    defaults = {"unbalanced_score": 0.66}

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part in context.score.parts:
            open_slurs: dict[tuple[str, int], tuple[object, Note]] = {}
            for measure in part.measures:
                for note in measure.iter_notes():
                    for spanner in note.spanners:
                        if spanner.kind != "slur":
                            continue
                        key = (note.voice, spanner.number)
                        if spanner.role is SpannerRole.START:
                            open_slurs[key] = (measure, note)
                        elif spanner.role is SpannerRole.STOP:
                            if key not in open_slurs:
                                yield self._orphan_stop(part, measure, note, spanner.number)
                            open_slurs.pop(key, None)
            for (voice, number), (measure, note) in open_slurs.items():
                yield self._orphan_start(part, measure, note, number, voice)

    def _orphan_start(
        self, part: object, measure: object, note: Note, number: int, voice: str
    ) -> Suggestion:
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.SLUR_REMOVE,
            severity=Severity.LOW,
            part=part,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            onset=note.onset,
            staff=note.staff,
            voice=voice,
            refs=(note.ref,),
            title="Slur never ends",
            explanation=(
                f"A slur (number {number}) starts on {note.pitch.name} and no note in this part "
                "closes it. Either the end of the curve was missed or this was not a slur at all."
            ),
            current_repr=f"{note.pitch.name} (slur start)",
            suggested_repr=f"{note.pitch.name} (no slur)",
            evidence=(
                self.evidence(
                    self.param("unbalanced_score"), f"slur {number} opens without closing"
                ),
            ),
            edits=(
                SetSlurOp(
                    ref=note.ref,
                    action=EditAction.REMOVE,
                    role=SpannerRole.START,
                    number=number,
                ),
            ),
        )

    def _orphan_stop(self, part: object, measure: object, note: Note, number: int) -> Suggestion:
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.SLUR_REMOVE,
            severity=Severity.LOW,
            part=part,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            onset=note.onset,
            staff=note.staff,
            voice=note.voice,
            refs=(note.ref,),
            title="Slur ends without starting",
            explanation=(
                f"A slur (number {number}) ends on {note.pitch.name} but nothing before it opens "
                "one. The start of the curve was probably lost at a system or page break."
            ),
            current_repr=f"{note.pitch.name} (slur end)",
            suggested_repr=f"{note.pitch.name} (no slur)",
            evidence=(
                self.evidence(
                    self.param("unbalanced_score"), f"slur {number} closes without opening"
                ),
            ),
            edits=(
                SetSlurOp(
                    ref=note.ref, action=EditAction.REMOVE, role=SpannerRole.STOP, number=number
                ),
            ),
        )
