"""Music-theoretic primitives the detectors reason with.

Pure functions over the IR: no XML, no detection policy, no thresholds that belong to a rule.
Everything here is independently testable and independently wrong-able.
"""

from .harmony import (
    CHORD_TEMPLATES,
    ChordMatch,
    best_repair,
    chord_fit,
    dissonance,
    identify_chord,
    interval_class_vector,
)
from .intervals import Interval, interval_between, is_consonant, melodic_difficulty
from .keys import (
    KRUMHANSL_MAJOR,
    KRUMHANSL_MINOR,
    KeyEstimate,
    diatonic_pitch_classes,
    estimate_key,
    is_diatonic,
    key_signature_pitch_classes,
    pitch_class_profile,
    scale_degree,
)
from .rhythm import (
    TUPLET_RATIOS,
    TYPE_LENGTHS,
    describe_quarters,
    dot_variants,
    duration_to_type,
    is_notatable,
    scale_variants,
    try_duration_to_type,
    type_length,
)
from .spelling import (
    AccidentalState,
    accidental_for_change,
    expected_alter,
    requires_printed_accidental,
    spell_pitch_class,
)
from .voice_leading import (
    ContourAnomaly,
    is_spike_shaped,
    local_roughness,
    roughness,
    spike_analysis,
    step_alternatives,
)

__all__ = [
    "CHORD_TEMPLATES",
    "KRUMHANSL_MAJOR",
    "KRUMHANSL_MINOR",
    "TUPLET_RATIOS",
    "TYPE_LENGTHS",
    "AccidentalState",
    "ChordMatch",
    "ContourAnomaly",
    "Interval",
    "KeyEstimate",
    "accidental_for_change",
    "best_repair",
    "chord_fit",
    "describe_quarters",
    "diatonic_pitch_classes",
    "dissonance",
    "dot_variants",
    "duration_to_type",
    "estimate_key",
    "expected_alter",
    "identify_chord",
    "interval_between",
    "interval_class_vector",
    "is_consonant",
    "is_diatonic",
    "is_notatable",
    "is_spike_shaped",
    "key_signature_pitch_classes",
    "local_roughness",
    "melodic_difficulty",
    "pitch_class_profile",
    "requires_printed_accidental",
    "roughness",
    "scale_degree",
    "scale_variants",
    "spell_pitch_class",
    "spike_analysis",
    "step_alternatives",
    "try_duration_to_type",
    "type_length",
]
