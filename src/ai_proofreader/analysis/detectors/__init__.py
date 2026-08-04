"""The rule set.

Each module here owns one family of errors. Detectors never import each other: two rules that
would need to share logic share a function in :mod:`ai_proofreader.music_theory` instead, so a
change to one can never silently alter another's behaviour.
"""

from .accidentals import (
    AccidentalConsistencyDetector,
    CarriedAccidentalDetector,
    ChromaticOutlierDetector,
)
from .motifs import MotifDeviationDetector
from .pitch import ContourSpikeDetector, HarmonicOutlierDetector
from .rhythm import MeasureDurationDetector, TupletDetector
from .structure import ClefPlausibilityDetector, KeySignatureConsistencyDetector
from .ties import SlurStructureDetector, TieIntegrityDetector
from .voices import VoiceCrossingDetector, VoiceOverlapDetector

__all__ = [
    "AccidentalConsistencyDetector",
    "CarriedAccidentalDetector",
    "ChromaticOutlierDetector",
    "ClefPlausibilityDetector",
    "ContourSpikeDetector",
    "HarmonicOutlierDetector",
    "KeySignatureConsistencyDetector",
    "MeasureDurationDetector",
    "MotifDeviationDetector",
    "SlurStructureDetector",
    "TieIntegrityDetector",
    "TupletDetector",
    "VoiceCrossingDetector",
    "VoiceOverlapDetector",
]
