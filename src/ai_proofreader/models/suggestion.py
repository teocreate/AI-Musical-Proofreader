"""Suggestions: what the proofreader tells the user, and how sure it is.

A suggestion is a complete case file, not a warning string. It names the location, states the
current reading and the proposed one, carries the evidence that produced it, and holds the exact
edits that would apply it. Everything the review UI shows comes from this object, and everything
the evaluation harness scores comes from it too.
"""

from __future__ import annotations

from enum import StrEnum
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .edit import EditOperation
from .events import ElementRef
from .geometry import SourceRegion
from .rational import Rational

__all__ = [
    "AnalysisReport",
    "Channel",
    "ConfidenceBreakdown",
    "Evidence",
    "Severity",
    "Suggestion",
    "SuggestionKind",
    "SuggestionStatus",
    "SuggestionTarget",
]


class SuggestionKind(StrEnum):
    """What kind of correction is being proposed. Drives grouping and filtering in the UI."""

    PITCH = "pitch"
    ACCIDENTAL_ADD = "accidental_add"
    ACCIDENTAL_REMOVE = "accidental_remove"
    ACCIDENTAL_CHANGE = "accidental_change"
    TIE_ADD = "tie_add"
    TIE_REMOVE = "tie_remove"
    SLUR_ADD = "slur_add"
    SLUR_REMOVE = "slur_remove"
    DOT_ADD = "dot_add"
    DOT_REMOVE = "dot_remove"
    DURATION = "duration"
    REST = "rest"
    TUPLET = "tuplet"
    VOICE = "voice"
    ARTICULATION = "articulation"
    DYNAMIC = "dynamic"
    CLEF = "clef"
    KEY_SIGNATURE = "key_signature"
    TIME_SIGNATURE = "time_signature"
    STRUCTURE = "structure"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


class Severity(StrEnum):
    """How badly the score is wrong if the suggestion is right.

    Independent of confidence: a missing tie we are 95% sure about is a low-severity certainty,
    while a bar that does not add up is high-severity even at 60%.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class Channel(StrEnum):
    """Independent sources of evidence that can support a suggestion."""

    MUSICAL = "musical"
    PATTERN = "pattern"
    VISUAL = "visual"
    AI = "ai"


class Evidence(BaseModel):
    """One reason to believe a suggestion, from one channel."""

    model_config = ConfigDict(frozen=True)

    channel: Channel
    score: float = Field(ge=0.0, le=1.0, description="Channel-local support in [0,1]")
    summary: str = Field(description="One line a musician can check against the page")
    detector: str = ""
    details: dict[str, str | float | int | bool] = Field(default_factory=dict)
    supports_current_reading: bool = Field(
        default=False,
        description="True when this evidence argues the existing notation is correct. Contrary "
        "evidence is kept rather than discarded so fusion can apply a disagreement penalty.",
    )


class ConfidenceBreakdown(BaseModel):
    """Per-channel scores and the fused result.

    ``None`` means the channel did not run or was not applicable — which is different from
    scoring zero, and the fusion weights are renormalized accordingly.
    """

    model_config = ConfigDict(frozen=True)

    musical: float | None = None
    pattern: float | None = None
    visual: float | None = None
    ai: float | None = None
    fused: float = Field(default=0.0, ge=0.0, le=1.0)
    calibrated: float = Field(default=0.0, ge=0.0, le=1.0)
    disagreement: float = Field(default=0.0, ge=0.0, le=1.0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def percent(self) -> int:
        """Calibrated confidence as the integer percentage shown in the UI."""
        return round(self.calibrated * 100)

    @property
    def available_channels(self) -> tuple[Channel, ...]:
        pairs = (
            (Channel.MUSICAL, self.musical),
            (Channel.PATTERN, self.pattern),
            (Channel.VISUAL, self.visual),
            (Channel.AI, self.ai),
        )
        return tuple(channel for channel, value in pairs if value is not None)


class SuggestionTarget(BaseModel):
    """Where in the score the suggestion applies."""

    model_config = ConfigDict(frozen=True)

    part_id: str
    part_name: str = ""
    measure_index: int = Field(ge=0)
    measure_number: str = ""
    staff: int = 1
    voice: str = "1"
    onset: Rational = Fraction(0)
    element_refs: tuple[ElementRef, ...] = ()
    region: SourceRegion | None = None

    def locator(self) -> str:
        """Compact human locator, e.g. ``Violin I m. 16 (staff 1, voice 2)``."""
        name = self.part_name or self.part_id
        detail = f"staff {self.staff}, voice {self.voice}"
        return f"{name} m. {self.measure_number or self.measure_index + 1} ({detail})"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class Suggestion(BaseModel):
    """A single proposed correction."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: SuggestionKind
    severity: Severity = Severity.MEDIUM
    detector: str = ""
    title: str = Field(description="One-line headline for the list view")
    explanation: str = Field(description="Why we think this, in the user's language")
    current_repr: str = Field(default="", description="What the score says now")
    suggested_repr: str = Field(default="", description="What we think it should say")
    target: SuggestionTarget
    evidence: tuple[Evidence, ...] = ()
    confidence: ConfidenceBreakdown = ConfidenceBreakdown()
    edits: tuple[EditOperation, ...] = ()
    status: SuggestionStatus = SuggestionStatus.PENDING

    @property
    def is_actionable(self) -> bool:
        """Whether accepting this suggestion actually changes the file.

        Some findings are advisory — "this bar does not add up, and we cannot tell you which of
        four notes is wrong". Those still belong in the list, but the Accept button is disabled.
        """
        return bool(self.edits)

    @property
    def sort_key(self) -> tuple[float, int, int, int]:
        """Ranking: confidence first, severity as the tie-break, then score order."""
        return (
            -self.confidence.calibrated,
            -self.severity.rank,
            self.target.measure_index,
            int(self.target.onset * 48),
        )

    def with_status(self, status: SuggestionStatus) -> Suggestion:
        return self.model_copy(update={"status": status})

    def with_confidence(self, confidence: ConfidenceBreakdown) -> Suggestion:
        return self.model_copy(update={"confidence": confidence})

    def one_line(self) -> str:
        arrow = f"{self.current_repr} → {self.suggested_repr}" if self.suggested_repr else ""
        return (
            f"[{self.confidence.percent:3d}%] {self.target.locator()}: {self.title} {arrow}".strip()
        )


class AnalysisReport(BaseModel):
    """Everything one analysis run produced. This is what gets written to JSON."""

    model_config = ConfigDict(frozen=True)

    score_summary: str = ""
    source_path: str = ""
    suggestions: tuple[Suggestion, ...] = ()
    parse_issues: tuple[str, ...] = ()
    detector_timings_ms: dict[str, float] = Field(default_factory=dict)
    total_ms: float = 0.0
    note_count: int = 0
    measure_count: int = 0
    version: str = ""

    def above(self, threshold: float) -> tuple[Suggestion, ...]:
        return tuple(s for s in self.suggestions if s.confidence.calibrated >= threshold)

    def by_kind(self) -> dict[SuggestionKind, int]:
        counts: dict[SuggestionKind, int] = {}
        for suggestion in self.suggestions:
            counts[suggestion.kind] = counts.get(suggestion.kind, 0) + 1
        return counts
