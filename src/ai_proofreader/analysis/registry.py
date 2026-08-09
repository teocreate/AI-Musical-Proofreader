"""Detector registry.

Ordered deliberately: structural rules first (a wrong clef invalidates every note-level finding
under it), then hard constraints, then the statistical rules. The order does not affect results —
detectors are independent — but it does affect what the user reads first when confidences tie.
"""

from __future__ import annotations

from ..config import AnalysisConfig, DetectorConfig
from .base import Detector
from .detectors import (
    AccidentalConsistencyDetector,
    CarriedAccidentalDetector,
    ChromaticOutlierDetector,
    ClefPlausibilityDetector,
    ContourSpikeDetector,
    EnharmonicSpellingDetector,
    HarmonicOutlierDetector,
    KeySignatureConsistencyDetector,
    MeasureDurationDetector,
    MotifDeviationDetector,
    SlurStructureDetector,
    TieIntegrityDetector,
    TupletDetector,
    VoiceCrossingDetector,
    VoiceOverlapDetector,
)

__all__ = [
    "DETECTOR_TYPES",
    "build_detectors",
    "default_detector_names",
    "detector_names",
]

#: Every detector shipped with the system, in reporting order.
DETECTOR_TYPES: tuple[type[Detector], ...] = (
    ClefPlausibilityDetector,
    KeySignatureConsistencyDetector,
    MeasureDurationDetector,
    TupletDetector,
    TieIntegrityDetector,
    SlurStructureDetector,
    VoiceOverlapDetector,
    AccidentalConsistencyDetector,
    EnharmonicSpellingDetector,
    MotifDeviationDetector,
    ContourSpikeDetector,
    HarmonicOutlierDetector,
    CarriedAccidentalDetector,
    ChromaticOutlierDetector,
    VoiceCrossingDetector,
)


def detector_names() -> tuple[str, ...]:
    """Every detector that exists, including the ones that are off by default."""
    return tuple(detector.name for detector in DETECTOR_TYPES)


def default_detector_names() -> tuple[str, ...]:
    """The detectors a user gets without touching configuration."""
    return tuple(detector.name for detector in DETECTOR_TYPES if detector.enabled_by_default)


def build_detectors(config: AnalysisConfig | None = None) -> list[Detector]:
    """Instantiate every enabled detector with its configuration.

    A detector runs when the configuration names it and leaves ``enabled`` true, or when the
    configuration is silent about it and the class ships enabled. Naming a detector is therefore
    how you switch on one of the off-by-default rules — including with an empty ``{}`` entry.
    """
    settings = config or AnalysisConfig()
    detectors: list[Detector] = []
    for detector_type in DETECTOR_TYPES:
        override = settings.detectors.get(detector_type.name)
        if override is None:
            if not detector_type.enabled_by_default:
                continue
            override = DetectorConfig()
        elif not override.enabled:
            continue
        detectors.append(detector_type(override))
    return detectors
