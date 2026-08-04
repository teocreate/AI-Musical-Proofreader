"""Score-level structural detectors: clefs and key signatures.

These fire rarely and matter enormously when they do. A clef read as treble instead of bass makes
every note in the part wrong, and no note-level rule will ever untangle that — but the *shape* of
the mistake is unmistakable: a part that suddenly lives on five ledger lines. Likewise a key
signature whose sharps were missed turns into hundreds of individually plausible naturals, which
only look wrong in aggregate.

Both rules therefore work on aggregates over many measures, and both refuse to fire on short
passages where an extreme register is ordinary.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from ...models import (
    Channel,
    Clef,
    ClefSign,
    KeySignature,
    Mode,
    Part,
    SetClefOp,
    SetKeyOp,
    Severity,
    Step,
    Suggestion,
    SuggestionKind,
)
from ..base import Detector, make_suggestion
from ..context import AnalysisContext

__all__ = ["ClefPlausibilityDetector", "KeySignatureConsistencyDetector"]

#: Clefs we are willing to propose, in the order a engraver would consider them.
_CANDIDATE_CLEFS: tuple[Clef, ...] = (
    Clef(sign=ClefSign.G, line=2),
    Clef(sign=ClefSign.F, line=4),
    Clef(sign=ClefSign.C, line=3),
    Clef(sign=ClefSign.C, line=4),
    Clef(sign=ClefSign.G, line=2, octave_change=-1),
    Clef(sign=ClefSign.F, line=4, octave_change=-1),
)


class ClefPlausibilityDetector(Detector):
    """Passages that would need far fewer ledger lines under a different clef."""

    name = "clef_plausibility"
    description = "Sustained extreme registers that a different clef would explain"
    kinds = (SuggestionKind.CLEF,)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.72,
        #: Mean ledger lines per note before a passage is considered implausible.
        "min_mean_ledger": 2.2,
        #: The alternative must reduce mean ledger lines by at least this factor.
        "improvement_factor": 3.0,
        #: Notes required before the statistic is trustworthy.
        "min_notes": 12.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part in context.score.parts:
            for staff in range(1, max(1, part.staves) + 1):
                yield from self._check_staff(context, part, staff)

    def _check_staff(
        self, context: AnalysisContext, part: Part, staff: int
    ) -> Iterable[Suggestion]:
        segments: dict[tuple[str, int, int], list[object]] = {}
        for measure in part.measures:
            for ref in context.notes_in(part.id, measure.index):
                if ref.note.staff != staff:
                    continue
                clef = context.clef_at(ref.measure, staff, ref.note.onset)
                if clef.sign in {ClefSign.PERCUSSION, ClefSign.TAB, ClefSign.NONE}:
                    continue
                key = (clef.sign.value, clef.line, clef.octave_change)
                segments.setdefault(key, []).append(ref)

        for key, refs in segments.items():
            if len(refs) < self.param("min_notes"):
                continue
            current = Clef(sign=ClefSign(key[0]), line=key[1], octave_change=key[2], staff=staff)
            mean_ledger = sum(
                current.ledger_lines(ref.note.pitch) for ref in refs  # type: ignore[attr-defined]
            ) / len(refs)
            if mean_ledger < self.param("min_mean_ledger"):
                continue

            best: tuple[Clef, float] | None = None
            for candidate in _CANDIDATE_CLEFS:
                if (candidate.sign, candidate.line, candidate.octave_change) == (
                    current.sign,
                    current.line,
                    current.octave_change,
                ):
                    continue
                score = sum(
                    candidate.ledger_lines(ref.note.pitch) for ref in refs  # type: ignore[attr-defined]
                ) / len(refs)
                if best is None or score < best[1]:
                    best = (candidate, score)
            if best is None or best[1] <= 0.0001:
                improvement = float("inf") if best and best[1] == 0 else 0.0
            else:
                improvement = mean_ledger / best[1]
            if best is None or improvement < self.param("improvement_factor"):
                continue

            first = min(refs, key=lambda ref: ref.absolute_onset)  # type: ignore[attr-defined]
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.CLEF,
                severity=Severity.CRITICAL,
                part=part,
                measure=first.measure,  # type: ignore[attr-defined]
                onset=first.note.onset,  # type: ignore[attr-defined]
                staff=staff,
                voice=first.note.voice,  # type: ignore[attr-defined]
                refs=(current.ref,) if current.ref >= 0 else (),
                title=f"Clef may be wrong — {best[0].describe()} fits far better",
                explanation=(
                    f"Under the current {current.describe()} clef, the {len(refs)} notes on this "
                    f"staff average {mean_ledger:.1f} ledger lines each. Under "
                    f"{best[0].describe()} they would average {best[1]:.1f}. A whole passage "
                    "living off the staff almost always means the clef itself was misread."
                ),
                current_repr=current.describe(),
                suggested_repr=best[0].describe(),
                evidence=(
                    self.evidence(
                        self.param("score"),
                        f"{mean_ledger:.1f} ledger lines/note now versus {best[1]:.1f} under "
                        f"{best[0].describe()}",
                        current_mean=mean_ledger,
                        candidate_mean=best[1],
                        notes=len(refs),
                    ),
                ),
                edits=(
                    (
                        SetClefOp(
                            ref=current.ref,
                            sign=best[0].sign.value,
                            line=best[0].line,
                            octave_change=best[0].octave_change,
                        ),
                    )
                    if current.ref >= 0
                    else ()
                ),
            )


class KeySignatureConsistencyDetector(Detector):
    """A key signature contradicted by the accidentals actually written in the part.

    The tell is systematic redundancy: if every F in a piece notated in C major carries a printed
    sharp, the signature lost its sharp. One or two such notes mean nothing; a hundred mean the
    signature is wrong.
    """

    name = "key_signature_consistency"
    description = "Key signatures contradicted by the accidentals written throughout the part"
    kinds = (SuggestionKind.KEY_SIGNATURE,)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.68,
        #: How consistently a letter must carry the same accidental, as a fraction of its notes.
        "min_ratio": 0.85,
        #: Minimum notes of that letter before the ratio means anything.
        "min_notes": 8.0,
        #: Measures the signature must be in force for.
        "min_measures": 8.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part in context.score.parts:
            yield from self._check_part(context, part)

    def _check_part(self, context: AnalysisContext, part: Part) -> Iterable[Suggestion]:
        spans = self._key_spans(part)
        for key, measures in spans:
            if len(measures) < self.param("min_measures"):
                continue
            altered: Counter[tuple[Step, int]] = Counter()
            totals: Counter[Step] = Counter()
            for measure in measures:
                for ref in context.notes_in(part.id, measure.index):  # type: ignore[attr-defined]
                    step = ref.note.pitch.step
                    totals[step] += 1
                    if ref.note.pitch.alter != key.alter_for(step):
                        altered[(step, ref.note.pitch.alter)] += 1

            implied: dict[Step, int] = {}
            for (step, alter), count in altered.items():
                if totals[step] < self.param("min_notes"):
                    continue
                if count / totals[step] >= self.param("min_ratio"):
                    implied[step] = alter
            if not implied:
                continue

            candidate = self._signature_for(key, implied)
            if candidate is None or candidate == key.fifths:
                continue

            evidence_text = ", ".join(
                f"{step.value}{'#' * alter if alter > 0 else 'b' * -alter}"
                for step, alter in sorted(implied.items(), key=lambda item: item[0].value)
            )
            first = measures[0]
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.KEY_SIGNATURE,
                severity=Severity.CRITICAL,
                part=part,
                measure=first,
                onset=first.events[0].onset if first.events else 0,
                staff=1,
                voice="1",
                refs=(key.ref,) if key.ref >= 0 else (),
                title=f"Key signature may be missing accidentals ({evidence_text})",
                explanation=(
                    f"Across {len(measures)} measures notated in {key.describe()}, almost every "
                    f"{evidence_text} is written with an accidental of its own. That is what a "
                    "part looks like when the key signature itself was not recognized. Changing "
                    f"the signature to {candidate:+d} sharps/flats would account for them."
                ),
                current_repr=key.describe(),
                suggested_repr=KeySignature(fifths=candidate, mode=key.mode).describe(),
                evidence=(
                    self.evidence(
                        self.param("score"),
                        f"{evidence_text} consistently accidental-marked over "
                        f"{len(measures)} measures",
                        measures=len(measures),
                    ),
                ),
                edits=(
                    (
                        SetKeyOp(
                            ref=key.ref,
                            fifths=candidate,
                            mode=key.mode.value if key.mode is not Mode.NONE else None,
                        ),
                    )
                    if key.ref >= 0
                    else ()
                ),
            )

    @staticmethod
    def _key_spans(part: Part) -> list[tuple[KeySignature, list[object]]]:
        """Group consecutive measures sharing a key signature."""
        spans: list[tuple[KeySignature, list[object]]] = []
        for measure in part.measures:
            key = measure.attributes.key
            if spans and spans[-1][0].fifths == key.fifths and spans[-1][0].mode == key.mode:
                spans[-1][1].append(measure)
            else:
                spans.append((key, [measure]))
        return spans

    @staticmethod
    def _signature_for(current: KeySignature, implied: dict[Step, int]) -> int | None:
        """Smallest signature consistent with the observed alterations, or ``None``."""
        for fifths in range(-7, 8):
            candidate = KeySignature(fifths=fifths, mode=current.mode)
            if all(candidate.alter_for(step) == alter for step, alter in implied.items()):
                existing = current.altered_steps
                # Do not propose dropping alterations the current signature already justifies.
                if all(
                    candidate.alter_for(step) == alter
                    for step, alter in existing.items()
                    if step not in implied
                ):
                    return fifths
        return None
