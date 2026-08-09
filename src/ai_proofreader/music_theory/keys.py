"""Key estimation and diatonic membership.

The notated key signature is the wrong thing to test notes against on its own: music modulates,
and a piece in C major spends whole pages in G. Testing against the signature alone produces a
flood of false positives on every secondary dominant. So every detector that cares about
"does this note belong" asks for a *local* key estimate over a sliding window, and gets back both
the key and how clear that estimate is — a chromatic passage yields a low-clarity estimate, and
detectors downweight themselves accordingly.

The estimator is Krumhansl–Schmuckler: correlate the window's duration-weighted pitch-class
histogram against the twenty-four rotated key profiles. It is old, it is cheap, and on tonal
music it is right often enough to be a useful prior — which is all it is used as.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from ..models import KeySignature, Mode, Pitch, Step

__all__ = [
    "KRUMHANSL_MAJOR",
    "KRUMHANSL_MINOR",
    "KeyEstimate",
    "diatonic_pitch_classes",
    "estimate_key",
    "is_diatonic",
    "key_signature_pitch_classes",
    "pitch_class_profile",
    "scale_degree",
]

#: Krumhansl–Kessler tonal hierarchy profiles (probe-tone ratings), major and minor.
KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float64
)
KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float64
)

_MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
_NATURAL_MINOR_INTERVALS = (0, 2, 3, 5, 7, 8, 10)
#: Minor keys in real music use raised 6 and 7 freely; treating those as non-diatonic would
#: flag every dominant chord in every minor-key piece.
_MINOR_EXTRA = (9, 11)

_PITCH_CLASS_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


@dataclass(frozen=True)
class KeyEstimate:
    """A local key hypothesis."""

    tonic_pitch_class: int
    mode: Mode
    correlation: float
    clarity: float
    #: Total duration (in quarter notes) the estimate was computed from.
    weight: float = 0.0

    @property
    def name(self) -> str:
        return f"{_PITCH_CLASS_NAMES[self.tonic_pitch_class % 12]} {self.mode.value}"

    @property
    def is_reliable(self) -> bool:
        """Whether this estimate is clear enough to argue with the notation.

        The thresholds are deliberately conservative: below them, detectors fall back to the
        notated key signature and reduce their own confidence.
        """
        return self.correlation >= 0.55 and self.clarity >= 0.06 and self.weight >= 3.0

    def pitch_classes(self) -> frozenset[int]:
        return diatonic_pitch_classes(self.tonic_pitch_class, self.mode)


def pitch_class_profile(
    pitches: list[Pitch] | tuple[Pitch, ...],
    weights: list[Fraction] | tuple[Fraction, ...] | None = None,
) -> np.ndarray:
    """Duration-weighted histogram of sounding pitch classes."""
    profile = np.zeros(12, dtype=np.float64)
    if weights is None:
        for pitch in pitches:
            profile[pitch.pitch_class] += 1.0
    else:
        for pitch, weight in zip(pitches, weights, strict=False):
            profile[pitch.pitch_class] += float(weight)
    return profile


def estimate_key(profile: np.ndarray, prior: KeySignature | None = None) -> KeyEstimate:
    """Best key for a pitch-class histogram.

    ``prior`` nudges the result toward the notated signature. The nudge is small (8%) — enough to
    break ties in ambiguous windows, not enough to override a clear modulation.
    """
    total = float(profile.sum())
    if total <= 0:
        mode = prior.mode if prior else Mode.MAJOR
        tonic = prior.tonic_pitch_class if prior else 0
        return KeyEstimate(
            tonic, mode if mode in {Mode.MAJOR, Mode.MINOR} else Mode.MAJOR, 0.0, 0.0
        )

    centered = profile - profile.mean()
    norm = float(np.linalg.norm(centered))
    if norm == 0:
        return KeyEstimate(0, Mode.MAJOR, 0.0, 0.0, total)

    scores: list[tuple[float, int, Mode]] = []
    for template, mode in ((KRUMHANSL_MAJOR, Mode.MAJOR), (KRUMHANSL_MINOR, Mode.MINOR)):
        template_centered = template - template.mean()
        template_norm = float(np.linalg.norm(template_centered))
        for tonic in range(12):
            rotated = np.roll(template_centered, tonic)
            correlation = float(np.dot(centered, rotated) / (norm * template_norm))
            scores.append((correlation, tonic, mode))

    if prior is not None:
        prior_tonic = prior.tonic_pitch_class
        prior_mode = Mode.MINOR if prior.mode is Mode.MINOR else Mode.MAJOR
        scores = [
            (score + (0.08 if (tonic == prior_tonic and mode is prior_mode) else 0.0), tonic, mode)
            for score, tonic, mode in scores
        ]

    scores.sort(reverse=True)
    best, tonic, mode = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    return KeyEstimate(
        tonic_pitch_class=tonic,
        mode=mode,
        correlation=best,
        clarity=max(0.0, best - runner_up),
        weight=total,
    )


def diatonic_pitch_classes(tonic_pitch_class: int, mode: Mode) -> frozenset[int]:
    """Pitch classes belonging to a key.

    Minor includes the raised sixth and seventh, because melodic and harmonic minor are not
    exceptions in tonal music — they are the norm.
    """
    tonic = tonic_pitch_class % 12
    if mode is Mode.MINOR or mode is Mode.AEOLIAN:
        intervals = _NATURAL_MINOR_INTERVALS + _MINOR_EXTRA
    elif mode is Mode.DORIAN:
        intervals = (0, 2, 3, 5, 7, 9, 10)
    elif mode is Mode.PHRYGIAN:
        intervals = (0, 1, 3, 5, 7, 8, 10)
    elif mode is Mode.LYDIAN:
        intervals = (0, 2, 4, 6, 7, 9, 11)
    elif mode is Mode.MIXOLYDIAN:
        intervals = (0, 2, 4, 5, 7, 9, 10)
    elif mode is Mode.LOCRIAN:
        intervals = (0, 1, 3, 5, 6, 8, 10)
    else:
        intervals = _MAJOR_INTERVALS
    return frozenset((tonic + interval) % 12 for interval in intervals)


def key_signature_pitch_classes(key: KeySignature) -> frozenset[int]:
    """Pitch classes of the scale the *signature* writes, ignoring modal inflections."""
    tonic = key.tonic_pitch_class
    intervals = _NATURAL_MINOR_INTERVALS if key.mode is Mode.MINOR else _MAJOR_INTERVALS
    return frozenset((tonic + interval) % 12 for interval in intervals)


def is_diatonic(pitch: Pitch, tonic_pitch_class: int, mode: Mode) -> bool:
    return pitch.pitch_class in diatonic_pitch_classes(tonic_pitch_class, mode)


def scale_degree(pitch: Pitch, key: KeySignature) -> int | None:
    """Scale degree 1–7 of ``pitch`` in ``key``'s scale, or ``None`` if chromatic."""
    tonic_step_name = key.tonic_name[0]
    tonic_index = Step(tonic_step_name).index
    degree = (pitch.step.index - tonic_index) % 7 + 1
    expected_alter = _expected_alter_in_key(pitch.step, key)
    return degree if pitch.alter == expected_alter else None


def _expected_alter_in_key(step: Step, key: KeySignature) -> int:
    return key.alter_for(step)
