"""Rhythm detectors: bars that do not add up, and tuplets that do not close.

These are the highest-precision rules in the system because they need no musical judgement at
all. A 4/4 bar containing three and a half beats is wrong, full stop — the only open question is
*which* note is wrong, and when there is exactly one edit that fixes the bar we can say so with
real confidence. When there are several, we say the bar is broken and let the user look, which is
still most of the value and costs no precision.
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

from ...models import (
    Channel,
    Chord,
    Event,
    InsertRestOp,
    Measure,
    Note,
    Rest,
    SetDurationOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ...music_theory.rhythm import (
    describe_quarters,
    dot_variants,
    is_notatable,
    scale_variants,
    try_duration_to_type,
)
from ..base import Detector, make_suggestion
from ..context import AnalysisContext

__all__ = ["MeasureDurationDetector", "TupletDetector"]


def _measured_events(measure: Measure, voice: str) -> list[Event]:
    return [
        event for event in measure.events if event.voice == voice and event.duration.is_measured
    ]


def _describe_event(event: Event) -> str:
    if isinstance(event, Note):
        return event.describe()
    if isinstance(event, Chord):
        return event.describe()
    if isinstance(event, Rest):
        return event.describe()
    return str(event)


class MeasureDurationDetector(Detector):
    """Bars whose contents do not match the time signature."""

    name = "measure_duration"
    description = "Measures whose voices do not fill (or overflow) the time signature"
    kinds = (
        SuggestionKind.DURATION,
        SuggestionKind.DOT_ADD,
        SuggestionKind.DOT_REMOVE,
        SuggestionKind.REST,
    )
    channel = Channel.MUSICAL
    defaults = {
        "single_repair_score": 0.86,
        "rest_repair_score": 0.68,
        "advisory_score": 0.62,
        #: Bars shorter than this many quarter notes are ignored; scores routinely contain
        #: fragmentary bars at repeat structures and cadenzas.
        "min_nominal_length": 0.5,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part, measure in context.score.iter_measures():
            nominal = measure.nominal_length
            if measure.implicit or nominal <= Fraction(
                self.param("min_nominal_length")
            ).limit_denominator(8):
                continue
            if not measure.events:
                continue

            voices = measure.voices
            primary = voices[0] if voices else "1"
            for voice in voices:
                events = _measured_events(measure, voice)
                if not events:
                    continue
                if len(events) == 1 and isinstance(events[0], Rest) and events[0].is_measure_rest:
                    continue  # a whole-measure rest is whatever the bar length is, by definition

                filled = max((event.end for event in events), default=Fraction(0))
                delta = nominal - filled
                if delta == 0:
                    continue
                if voice != primary and delta > 0:
                    # A secondary voice that stops early is ordinary notation, not an error.
                    continue

                yield from self._report(
                    context, part, measure, voice, events, nominal, filled, delta
                )

    def _report(
        self,
        context: AnalysisContext,
        part: object,
        measure: Measure,
        voice: str,
        events: list[Event],
        nominal: Fraction,
        filled: Fraction,
        delta: Fraction,
    ) -> Iterable[Suggestion]:
        divisions = measure.attributes.divisions
        repairs = self._single_event_repairs(events, delta, divisions)
        staff = events[0].staff
        direction = "short of" if delta > 0 else "longer than"
        summary = (
            f"voice {voice} fills {describe_quarters(filled)} of a "
            f"{measure.attributes.time.describe() if measure.attributes.time else '?'} bar "
            f"({abs(delta)} quarter notes {direction} the bar)"
        )

        if len(repairs) == 1:
            event, operation, label, kind = repairs[0]
            yield make_suggestion(
                detector=self.name,
                kind=kind,
                severity=Severity.HIGH,
                part=part,  # type: ignore[arg-type]
                measure=measure,
                onset=event.onset,
                staff=event.staff,
                voice=voice,
                refs=(event.ref,),
                title=f"Measure does not add up — {label}",
                explanation=(
                    f"This bar is {summary}. Exactly one change fixes it: {label}. "
                    "A missed dot or a mis-counted flag is the usual cause after OMR."
                ),
                current_repr=_describe_event(event),
                suggested_repr=label,
                evidence=(
                    self.evidence(
                        self.param("single_repair_score"),
                        f"{summary}; one unique repair available",
                        delta=float(delta),
                        nominal=float(nominal),
                    ),
                ),
                edits=(operation,),
            )
            return

        if not repairs and delta > 0 and is_notatable(delta):
            last = events[-1]
            guess = try_duration_to_type(delta)
            assert guess is not None
            note_type, dots, _ = guess
            ticks = delta * divisions
            if ticks.denominator == 1:
                yield make_suggestion(
                    detector=self.name,
                    kind=SuggestionKind.REST,
                    severity=Severity.HIGH,
                    part=part,  # type: ignore[arg-type]
                    measure=measure,
                    onset=last.end,
                    staff=staff,
                    voice=voice,
                    refs=(last.ref,),
                    title=f"Measure is missing {describe_quarters(delta)}",
                    explanation=(
                        f"This bar is {summary}. No single note length explains the gap, but a "
                        f"{describe_quarters(delta)} rest at the end would close it — a dropped "
                        "rest glyph is a common OMR omission."
                    ),
                    current_repr=f"{describe_quarters(filled)} of {describe_quarters(nominal)}",
                    suggested_repr=f"add a {describe_quarters(delta)} rest",
                    evidence=(
                        self.evidence(
                            self.param("rest_repair_score"),
                            f"{summary}; gap is exactly a writable rest",
                            delta=float(delta),
                        ),
                    ),
                    edits=(
                        InsertRestOp(
                            ref=last.ref,
                            note_type=note_type,
                            dots=dots,
                            ticks=int(ticks),
                            quarter_length=delta,
                        ),
                    ),
                )
                return

        options = ", ".join(label for _, _, label, _ in repairs[:4]) if repairs else "none obvious"
        yield make_suggestion(
            detector=self.name,
            kind=SuggestionKind.DURATION,
            severity=Severity.HIGH,
            part=part,  # type: ignore[arg-type]
            measure=measure,
            onset=Fraction(0),
            staff=staff,
            voice=voice,
            refs=tuple(event.ref for event in events),
            title="Measure does not add up",
            explanation=(
                f"This bar is {summary}. Several different edits would fix it "
                f"({options}), so it needs a human eye rather than an automatic correction."
            ),
            current_repr=f"{describe_quarters(filled)} of {describe_quarters(nominal)}",
            suggested_repr="",
            evidence=(
                self.evidence(
                    self.param("advisory_score"),
                    f"{summary}; {len(repairs)} candidate repairs",
                    delta=float(delta),
                    candidates=len(repairs),
                ),
            ),
        )

    def _single_event_repairs(
        self, events: list[Event], delta: Fraction, divisions: int
    ) -> list[tuple[Event, SetDurationOp, str, SuggestionKind]]:
        """Every one-note duration change that would make the bar come out exactly right."""
        repairs: list[tuple[Event, SetDurationOp, str, SuggestionKind]] = []
        for event in events:
            current = event.duration.quarter_length
            if current <= 0:
                continue
            candidates: list[tuple[Fraction, int, str | None, SuggestionKind]] = []

            for dots, new_length in dot_variants(current, event.duration.dots):
                kind = (
                    SuggestionKind.DOT_ADD
                    if dots > event.duration.dots
                    else SuggestionKind.DOT_REMOVE
                )
                candidates.append((new_length, dots, event.duration.note_type, kind))

            for new_length in scale_variants(current):
                guess = try_duration_to_type(new_length)
                if guess is None:
                    continue
                note_type, dots, tuplet = guess
                if tuplet is not None:
                    continue
                candidates.append((new_length, dots, note_type, SuggestionKind.DURATION))

            for new_length, dots, note_type, kind in candidates:
                if new_length - current != delta or new_length <= 0:
                    continue
                ticks = new_length * divisions
                if ticks.denominator != 1:
                    continue
                label = f"change {_describe_event(event)} to {describe_quarters(new_length)}"
                repairs.append(
                    (
                        event,
                        SetDurationOp(
                            ref=event.ref,
                            note_type=note_type,
                            dots=dots,
                            ticks=int(ticks),
                            quarter_length=new_length,
                        ),
                        label,
                        kind,
                    )
                )
        return repairs


class TupletDetector(Detector):
    """Tuplet groups that do not sum to a writable duration, or brackets that never close."""

    name = "tuplet_integrity"
    description = "Incomplete or inconsistent tuplet groups"
    kinds = (SuggestionKind.TUPLET,)
    channel = Channel.MUSICAL
    defaults = {"incomplete_score": 0.74, "unclosed_score": 0.7}

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for stream in context.streams:
            yield from self._check_groups(stream)
        for part, measure in context.score.iter_measures():
            yield from self._check_brackets(part, measure)

    def _check_groups(self, stream: object) -> Iterable[Suggestion]:
        entries = getattr(stream, "entries", [])
        part = stream.part
        run: list[object] = []
        ratio: tuple[int, int] | None = None

        def flush() -> Iterable[Suggestion]:
            if not run or ratio is None:
                return
            total = sum((entry.duration for entry in run), Fraction(0))
            if is_notatable(total, allow_tuplets=False):
                return
            first = run[0]
            element = first.note or first.rest
            if element is None:
                return
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.TUPLET,
                severity=Severity.MEDIUM,
                part=part,
                measure=first.measure,
                onset=element.onset,
                staff=element.staff,
                voice=element.voice,
                refs=tuple(
                    (entry.note or entry.rest).ref  # type: ignore[union-attr]
                    for entry in run
                    if (entry.note or entry.rest) is not None
                ),
                title=f"Incomplete {ratio[0]}:{ratio[1]} tuplet",
                explanation=(
                    f"{len(run)} notes marked as a {ratio[0]}:{ratio[1]} tuplet total "
                    f"{total} quarter notes, which is not a duration that can be written as a "
                    "complete group. OMR usually causes this by missing one note of the tuplet "
                    "or by extending the bracket over a note that is not part of it."
                ),
                current_repr=f"{len(run)} notes totalling {total}",
                suggested_repr="",
                evidence=(
                    self.evidence(
                        self.param("incomplete_score"),
                        f"{ratio[0]}:{ratio[1]} group of {len(run)} notes sums to {total}",
                        total=float(total),
                        count=len(run),
                    ),
                ),
            )

        for entry in entries:
            element = entry.note or entry.rest
            modification = element.duration.time_modification if element is not None else None
            current = (
                (modification.actual_notes, modification.normal_notes) if modification else None
            )
            if current != ratio:
                yield from flush()
                run = []
                ratio = current
            if ratio is not None:
                run.append(entry)
        yield from flush()

    def _check_brackets(self, part: object, measure: Measure) -> Iterable[Suggestion]:
        """Tuplet start/stop markers that do not pair up within the measure."""
        open_brackets: dict[tuple[str, int], object] = {}
        for note in measure.iter_notes():
            for spanner in note.spanners:
                if spanner.kind != "tuplet":
                    continue
                key = (note.voice, spanner.number)
                if spanner.role.value == "start":
                    open_brackets[key] = note
                elif spanner.role.value == "stop":
                    open_brackets.pop(key, None)

        for (voice, number), note in open_brackets.items():
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.TUPLET,
                severity=Severity.MEDIUM,
                part=part,  # type: ignore[arg-type]
                measure=measure,
                onset=note.onset,  # type: ignore[union-attr]
                staff=note.staff,  # type: ignore[union-attr]
                voice=voice,
                refs=(note.ref,),  # type: ignore[union-attr]
                title="Tuplet bracket never closes",
                explanation=(
                    f"A tuplet bracket (number {number}) starts here and has no matching end in "
                    "this measure. The group is either missing notes or the bracket was read "
                    "across a barline it does not cross."
                ),
                current_repr=note.describe(),  # type: ignore[union-attr]
                suggested_repr="",
                evidence=(
                    self.evidence(
                        self.param("unclosed_score"),
                        f"tuplet {number} in voice {voice} opens without closing",
                    ),
                ),
            )
