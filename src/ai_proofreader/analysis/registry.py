"""Detector registry.

Ordered deliberately: structural rules first (a wrong clef invalidates every note-level finding
under it), then hard constraints, then the statistical rules. The order does not affect results —
detectors are independent — but it does affect what the user reads first when confidences tie.
"""

from __future__ import annotations

from ..config import AnalysisConfig
from .base import Detector
from .detectors import (
    AccidentalConsistencyDetector,
    CarriedAccidentalDetector,
    ChromaticOutlierDetector,
    ClefPlausibilityDetector,
    ContourSpikeDetector,
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

__all__ = ["DETECTOR_TYPES", "build_detectors", "detector_names"]

#: Every detector shipped with the system.
DETECTOR_TYPES: tuple[type[Detector], ...] = (
    ClefPlausibilityDetector,
    KeySignatureConsistencyDetector,
    MeasureDurationDetector,
    TupletDetector,
    TieIntegrityDetector,
    SlurStructureDetector,
    VoiceOverlapDetector,
    AccidentalConsistencyDetector,
    MotifDeviationDetector,
    ContourSpikeDetector,
    HarmonicOutlierDetector,
    CarriedAccidentalDetector,
    ChromaticOutlierDetector,
    VoiceCrossingDetector,
)


def detector_names() -> tuple[str, ...]:
    return tuple(detector.name for detector in DETECTOR_TYPES)


def build_detectors(config: AnalysisConfig | None = None) -> list[Detector]:
    """Instantiate every enabled detector with its configuration."""
    settings = config or AnalysisConfig()
    detectors: list[Detector] = []
    for detector_type in DETECTOR_TYPES:
        detector_config = settings.for_detector(detector_type.name)
        if not detector_config.enabled:
            continue
        detectors.append(detector_type(detector_config))
    return detectors
