"""Detector base class and suggestion construction helpers.

A detector is a pure function from :class:`AnalysisContext` to suggestions. It may not mutate the
context, may not call other detectors, and may not decide whether the user sees its output — that
is the fusion layer's job. Keeping the contract this narrow is what makes it possible to add a
rule without regressing the ones already there, and to test each one in isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from fractions import Fraction
from hashlib import blake2b
from typing import ClassVar

from ..config import DetectorConfig
from ..models import (
    Channel,
    EditOperation,
    ElementRef,
    Evidence,
    Measure,
    Part,
    Severity,
    Suggestion,
    SuggestionKind,
    SuggestionTarget,
)
from .context import AnalysisContext

__all__ = ["Detector", "make_suggestion", "suggestion_id"]


def suggestion_id(
    detector: str,
    kind: SuggestionKind,
    part_id: str,
    measure_index: int,
    onset: Fraction,
    refs: Sequence[ElementRef] = (),
) -> str:
    """A stable, short identifier for a suggestion.

    Stability across runs matters more than it looks: the UI remembers which suggestions the user
    dismissed, the evaluation harness matches predictions to ground truth by id, and a re-analysis
    after applying one fix must not renumber everything else.
    """
    payload = (
        f"{detector}|{kind.value}|{part_id}|{measure_index}|{onset}|{','.join(map(str, refs))}"
    )
    digest = blake2b(payload.encode("utf-8"), digest_size=5).hexdigest()
    return f"{kind.value[:3]}-{digest}"


def make_suggestion(
    *,
    detector: str,
    kind: SuggestionKind,
    severity: Severity,
    part: Part,
    measure: Measure,
    onset: Fraction,
    title: str,
    explanation: str,
    current_repr: str = "",
    suggested_repr: str = "",
    staff: int = 1,
    voice: str = "1",
    refs: Sequence[ElementRef] = (),
    evidence: Sequence[Evidence] = (),
    edits: Sequence[EditOperation] = (),
) -> Suggestion:
    """Assemble a suggestion with a stable id and a filled-in target."""
    target = SuggestionTarget(
        part_id=part.id,
        part_name=part.display_name,
        measure_index=measure.index,
        measure_number=measure.number,
        staff=staff,
        voice=voice,
        onset=onset,
        element_refs=tuple(refs),
    )
    return Suggestion(
        id=suggestion_id(detector, kind, part.id, measure.index, onset, refs),
        kind=kind,
        severity=severity,
        detector=detector,
        title=title,
        explanation=explanation,
        current_repr=current_repr,
        suggested_repr=suggested_repr,
        target=target,
        evidence=tuple(evidence),
        edits=tuple(edits),
    )


class Detector(ABC):
    """Base class for all rules.

    Subclasses set the class attributes, implement :meth:`run`, and read tunables through
    :meth:`param` so ``scripts/evaluate.py`` can sweep them.
    """

    #: Stable identifier, used in config, reports and evidence.
    name: ClassVar[str] = "detector"
    #: Human-readable one-liner shown in the UI's detector list.
    description: ClassVar[str] = ""
    #: Which suggestion kinds this detector can emit.
    kinds: ClassVar[tuple[SuggestionKind, ...]] = ()
    #: Which evidence channel its findings belong to.
    channel: ClassVar[Channel] = Channel.MUSICAL
    #: Default values for tunables; overridden per-run by ``DetectorConfig.params``.
    defaults: ClassVar[dict[str, float]] = {}
    #: True when the rule needs every part at once (vertical harmony, cross-part agreement).
    #: Part-local rules can be sharded across processes; cross-part rules cannot.
    cross_part: ClassVar[bool] = False

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()

    @abstractmethod
    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        """Analyse the score and yield suggestions. Must not mutate ``context``."""

    # -- helpers -------------------------------------------------------------------

    def param(self, key: str, default: float | None = None) -> float:
        """Read a tunable: run config first, then the class default, then ``default``."""
        if key in self.config.params:
            return self.config.params[key]
        if key in self.defaults:
            return self.defaults[key]
        if default is None:
            raise KeyError(f"{self.name}: no value or default for parameter {key!r}")
        return default

    def evidence(
        self,
        score: float,
        summary: str,
        channel: Channel | None = None,
        supports_current_reading: bool = False,
        **details: str | float | int | bool,
    ) -> Evidence:
        """Build an evidence item attributed to this detector, with its weight applied."""
        weighted = max(0.0, min(1.0, score * self.config.weight))
        return Evidence(
            channel=channel or self.channel,
            score=weighted,
            summary=summary,
            detector=self.name,
            details=details,
            supports_current_reading=supports_current_reading,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name}>"
