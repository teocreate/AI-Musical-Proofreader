"""Vertical analysis: what chord is sounding, and how badly one note fits it.

Used by the harmonic-outlier detector. The question it answers is narrow and deliberately so:
*given the other notes sounding at this instant, is there a one-semitone change to this note that
turns a mess into a chord?* That is the shape of a missed accidental, and it is a much safer
question than "is this chord correct", which no rule system answers well.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CHORD_TEMPLATES",
    "ChordMatch",
    "chord_fit",
    "dissonance",
    "identify_chord",
    "interval_class_vector",
]

#: Interval patterns above the root, in semitones, ordered from most to least common so ties
#: resolve toward the likelier reading.
CHORD_TEMPLATES: tuple[tuple[str, tuple[int, ...], float], ...] = (
    ("major triad", (0, 4, 7), 1.00),
    ("minor triad", (0, 3, 7), 1.00),
    ("dominant seventh", (0, 4, 7, 10), 0.98),
    ("major seventh", (0, 4, 7, 11), 0.92),
    ("minor seventh", (0, 3, 7, 10), 0.94),
    ("diminished triad", (0, 3, 6), 0.88),
    ("half-diminished seventh", (0, 3, 6, 10), 0.88),
    ("diminished seventh", (0, 3, 6, 9), 0.86),
    ("augmented triad", (0, 4, 8), 0.72),
    ("minor-major seventh", (0, 3, 7, 11), 0.70),
    ("suspended fourth", (0, 5, 7), 0.80),
    ("suspended second", (0, 2, 7), 0.78),
    ("major sixth", (0, 4, 7, 9), 0.84),
    ("minor sixth", (0, 3, 7, 9), 0.82),
)

_PITCH_CLASS_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

#: Weight of each interval class in the dissonance measure (unison … tritone).
_INTERVAL_CLASS_DISSONANCE = (0.0, 1.0, 0.55, 0.15, 0.15, 0.25, 0.75)


@dataclass(frozen=True)
class ChordMatch:
    """A chord identification: name, root, and how completely the sounding notes match it."""

    name: str
    root: int
    #: Fraction of template tones present, times the template's own plausibility weight.
    fit: float
    #: Pitch classes sounding that the template does not contain.
    extra: frozenset[int]
    #: Template tones that are missing.
    missing: frozenset[int]

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def label(self) -> str:
        return f"{_PITCH_CLASS_NAMES[self.root % 12]} {self.name}"


def interval_class_vector(pitch_classes: frozenset[int] | set[int]) -> tuple[int, ...]:
    """Count of each interval class (1–6) present in the set."""
    counts = [0] * 7
    classes = sorted(pitch_classes)
    for index, first in enumerate(classes):
        for second in classes[index + 1 :]:
            difference = (second - first) % 12
            counts[min(difference, 12 - difference)] += 1
    return tuple(counts)


def dissonance(pitch_classes: frozenset[int] | set[int]) -> float:
    """Normalized dissonance of a simultaneity, ``0`` (consonant) to ``1``."""
    if len(pitch_classes) < 2:
        return 0.0
    counts = interval_class_vector(pitch_classes)
    total = sum(counts)
    if total == 0:
        return 0.0
    weighted = sum(
        count * weight for count, weight in zip(counts, _INTERVAL_CLASS_DISSONANCE, strict=True)
    )
    return min(1.0, weighted / total)


def identify_chord(pitch_classes: frozenset[int] | set[int]) -> ChordMatch | None:
    """Best chord match for a set of sounding pitch classes."""
    classes = frozenset(pc % 12 for pc in pitch_classes)
    if len(classes) < 2:
        return None

    best: ChordMatch | None = None
    for root in classes:
        for name, intervals, weight in CHORD_TEMPLATES:
            template = frozenset((root + interval) % 12 for interval in intervals)
            present = classes & template
            missing = template - classes
            extra = classes - template
            if len(present) < 2:
                continue
            coverage = len(present) / len(template)
            penalty = 0.25 * len(extra)
            fit = max(0.0, (coverage * weight) - penalty)
            if best is None or fit > best.fit:
                best = ChordMatch(
                    name=name,
                    root=root,
                    fit=fit,
                    extra=frozenset(extra),
                    missing=frozenset(missing),
                )
    return best


def chord_fit(pitch_classes: frozenset[int] | set[int]) -> float:
    """Convenience: fit score of the best chord match, ``0`` if nothing matches."""
    match = identify_chord(pitch_classes)
    return match.fit if match else 0.0


def best_repair(
    others: frozenset[int] | set[int], candidate: int, alternatives: tuple[int, ...]
) -> tuple[int, float, float] | None:
    """Would replacing ``candidate`` with one of ``alternatives`` improve the harmony?

    Returns ``(alternative, fit_before, fit_after)`` for the best improvement, or ``None`` when
    no alternative helps. This is the exact question the harmonic-outlier detector asks.
    """
    before = chord_fit(frozenset(others) | {candidate % 12})
    best: tuple[int, float, float] | None = None
    for alternative in alternatives:
        after = chord_fit(frozenset(others) | {alternative % 12})
        if after > before and (best is None or after > best[2]):
            best = (alternative, before, after)
    return best
