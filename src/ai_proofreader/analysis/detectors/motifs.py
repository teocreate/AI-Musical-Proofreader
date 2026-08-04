"""Motif-deviation detector.

The rule the whole product was pitched on: *this figure appears three times and one copy is
different by one semitone*. It is the strongest musical evidence available without the scan,
because it does not depend on any assumption about style, key or genre — only on the observation
that composers repeat themselves and recognition errors do not.

Scoring rises with the number of agreeing occurrences and falls for larger deviations. Three
agreeing copies of a four-note figure is already strong; six copies is close to conclusive.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...models import (
    ALTER_ACCIDENTAL,
    Channel,
    SetPitchOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ..base import Detector, make_suggestion
from ..context import AnalysisContext
from ..patterns import MotifDeviation, MotifIndex

__all__ = ["MotifDeviationDetector"]


class MotifDeviationDetector(Detector):
    """One occurrence of a repeated figure disagreeing with its siblings at a single note."""

    name = "motif_deviation"
    description = "A repeated melodic figure whose copies disagree at exactly one note"
    kinds = (SuggestionKind.PITCH, SuggestionKind.ACCIDENTAL_CHANGE)
    channel = Channel.PATTERN
    defaults = {
        "base_score": 0.62,
        #: Added per agreeing occurrence beyond the second.
        "support_bonus": 0.09,
        #: Chromatic deviations (a missed accidental) are more likely than notehead slips and
        #: score slightly higher for the same support.
        "chromatic_bonus": 0.05,
        "max_score": 0.93,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        config = context.config
        index = MotifIndex(
            length=config.motif_min_length, min_occurrences=config.motif_min_occurrences
        )
        for stream in context.streams:
            index.add_stream(stream)

        best_per_note: dict[int, MotifDeviation] = {}
        for deviation in index.deviations():
            note = deviation.entry.note
            if note is None or note.ref < 0:
                continue
            existing = best_per_note.get(note.ref)
            if existing is None or deviation.support > existing.support:
                best_per_note[note.ref] = deviation

        for deviation in best_per_note.values():
            suggestion = self._build(context, deviation)
            if suggestion is not None:
                yield suggestion

    def _build(self, context: AnalysisContext, deviation: MotifDeviation) -> Suggestion | None:
        entry = deviation.entry
        note = entry.note
        if note is None or note.is_grace:
            return None
        expected = deviation.expected_pitch
        if expected.midi == note.pitch.midi:
            return None

        part = context.streams_by_key.get(deviation.occurrence.stream_key)
        if part is None:
            return None

        score = min(
            self.param("max_score"),
            self.param("base_score")
            + self.param("support_bonus") * max(0, deviation.support - 2)
            + (self.param("chromatic_bonus") if deviation.kind == "chromatic" else 0.0),
        )

        bars = ", ".join(
            f"m. {occurrence.entries[0].measure.number}" for occurrence in deviation.agreeing[:3]
        )
        extra = "" if len(deviation.agreeing) <= 3 else f" and {len(deviation.agreeing) - 3} more"
        if deviation.kind == "chromatic":
            headline = (
                f"Repeated figure differs by {abs(deviation.delta)} semitone"
                f"{'s' if abs(deviation.delta) != 1 else ''}"
            )
            reason = (
                f"The same figure — same staff positions, same rhythm — appears at {bars}{extra} "
                f"with {expected.name} where this copy has {note.pitch.name}. The notehead sits "
                "on the same line in every copy, so the difference is an accidental, and a "
                "missed accidental is far likelier than a deliberate one-note variant."
            )
            kind = SuggestionKind.ACCIDENTAL_CHANGE
        else:
            headline = (
                f"Repeated figure differs by {abs(deviation.delta)} staff position"
                f"{'s' if abs(deviation.delta) != 1 else ''}"
            )
            reason = (
                f"The same figure appears at {bars}{extra} with {expected.name} in this position. "
                f"This copy has {note.pitch.name}, one notehead off — the classic result of a "
                "notehead read onto the neighbouring line or space."
            )
            kind = SuggestionKind.PITCH

        return make_suggestion(
            detector=self.name,
            kind=kind,
            severity=Severity.HIGH,
            part=part.part,
            measure=entry.measure,
            onset=note.onset,
            staff=note.staff,
            voice=note.voice,
            refs=(note.ref,),
            title=headline,
            explanation=reason,
            current_repr=note.pitch.name,
            suggested_repr=expected.name,
            evidence=(
                self.evidence(
                    score,
                    f"{deviation.support} other occurrences of this figure read {expected.name}",
                    support=deviation.support,
                    kind=deviation.kind,
                    delta=deviation.delta,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=expected.step,
                    alter=expected.alter,
                    octave=expected.octave,
                    accidental=(
                        ALTER_ACCIDENTAL.get(expected.alter)
                        if deviation.kind == "chromatic"
                        else None
                    ),
                    set_accidental=deviation.kind == "chromatic",
                ),
            ),
        )
