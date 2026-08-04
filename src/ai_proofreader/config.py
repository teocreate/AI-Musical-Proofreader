"""Tunable configuration.

Every threshold that decides whether a user sees a suggestion lives here rather than as a literal
in a detector. That is not tidiness for its own sake: the precision claim in the README is a
measurement against a corpus at a specific operating point, and an operating point you cannot
write down is one you cannot defend. ``scripts/evaluate.py`` sweeps these values; changing a
number in a detector body instead of here makes the sweep lie.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_CONFIG",
    "AnalysisConfig",
    "DetectorConfig",
    "FusionConfig",
    "ProofreaderConfig",
    "UIConfig",
]


class DetectorConfig(BaseModel):
    """Per-detector switches. ``params`` overrides a detector's own named thresholds."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    #: Multiplier on this detector's musical-channel score. Lower it for rules you trust less
    #: on your repertoire rather than turning them off entirely.
    weight: float = Field(default=1.0, ge=0.0, le=2.0)
    params: dict[str, float] = Field(default_factory=dict)


class AnalysisConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Width, in measures, of the sliding window used for local key estimation. Four bars is
    #: long enough to establish a key and short enough to follow a modulation.
    key_window_measures: int = Field(default=4, ge=1, le=32)

    #: Suggestions below this calibrated confidence are dropped before the user ever sees them.
    #: The product promise is precision; a long tail of 20% guesses is what makes proofreading
    #: tools get uninstalled.
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)

    #: Hard cap on reported suggestions, applied after ranking.
    max_suggestions: int = Field(default=1000, ge=1)

    #: Shortest motif, in notes, that pattern matching will consider.
    motif_min_length: int = Field(default=4, ge=2, le=32)
    #: How many times a motif must appear before a deviation in one occurrence means anything.
    motif_min_occurrences: int = Field(default=3, ge=2, le=16)
    #: Maximum number of differing positions for two motif occurrences to still be "the same".
    #: Above one, a "deviation" is a variation and saying so would be a guess.
    motif_max_deviations: int = Field(default=1, ge=1, le=4)

    #: Run detectors across processes. Turn off for profiling or debugging.
    parallel: bool = True
    #: Score size (in notes) below which parallelism costs more than it saves.
    parallel_threshold_notes: int = Field(default=4000, ge=0)

    detectors: dict[str, DetectorConfig] = Field(default_factory=dict)

    def for_detector(self, name: str) -> DetectorConfig:
        return self.detectors.get(name, DetectorConfig())


class FusionConfig(BaseModel):
    """Weights and calibration for combining evidence channels."""

    model_config = ConfigDict(frozen=True)

    musical_weight: float = Field(default=1.0, ge=0.0)
    pattern_weight: float = Field(default=1.1, ge=0.0)
    visual_weight: float = Field(default=1.4, ge=0.0)
    ai_weight: float = Field(default=1.2, ge=0.0)

    #: Fraction of the confidence that fully-certain contrary evidence removes. A verifier that
    #: has looked at the page and reports the existing notation is correct should be close to
    #: decisive, so this is deliberately high.
    disagreement_penalty: float = Field(default=0.9, ge=0.0, le=1.0)

    #: How much a corroborating channel can lift the anchor channel's score, as a fraction of the
    #: remaining headroom. At 0.5, a second channel fully agreeing closes half the gap to 1.0.
    corroboration_gain: float = Field(default=0.5, ge=0.0, le=1.0)

    #: Logistic calibration ``1 / (1 + exp(-(slope * (x - midpoint))))`` mapping the raw fused
    #: score onto a probability. Fitted on the regression corpus, not guessed.
    calibration_slope: float = Field(default=6.0, gt=0.0)
    calibration_midpoint: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Confidence ceiling with no visual confirmation. Musical reasoning alone should never
    #: claim near-certainty about what is printed on a page it has not looked at.
    musical_only_ceiling: float = Field(default=0.85, ge=0.0, le=1.0)


class UIConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Confidence below which a suggestion is hidden behind the "show low confidence" toggle.
    review_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    staff_space_pixels: float = Field(default=8.0, gt=0.0)
    dark_mode: bool = True


class ProofreaderConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    analysis: AnalysisConfig = AnalysisConfig()
    fusion: FusionConfig = FusionConfig()
    ui: UIConfig = UIConfig()

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Read a JSON config file."""
        data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return target


DEFAULT_CONFIG = ProofreaderConfig()
