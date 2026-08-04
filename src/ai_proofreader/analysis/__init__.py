"""Musical analysis: the context, the rules, and the registry that assembles them."""

from .base import Detector, make_suggestion, suggestion_id
from .context import AnalysisContext, NoteRef, StreamEntry, VerticalSlice, VoiceStream
from .registry import DETECTOR_TYPES, build_detectors, detector_names

__all__ = [
    "DETECTOR_TYPES",
    "AnalysisContext",
    "Detector",
    "NoteRef",
    "StreamEntry",
    "VerticalSlice",
    "VoiceStream",
    "build_detectors",
    "detector_names",
    "make_suggestion",
    "suggestion_id",
]
