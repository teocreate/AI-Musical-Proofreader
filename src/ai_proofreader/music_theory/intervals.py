"""Intervals, and what makes one suspicious.

An OMR pitch error leaves a signature in the intervals around it. Reading a notehead one staff
position too high turns a step into a leap and the following leap into a step; a missed sharp
turns a perfect fourth into an augmented fourth. So interval *quality* — not just size in
semitones — is a primary detection signal, and it needs the diatonic spelling to be computed at
all.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Pitch

__all__ = [
    "IMPERFECT_CONSONANCES",
    "PERFECT_CONSONANCES",
    "Interval",
    "interval_between",
    "is_consonant",
    "melodic_difficulty",
]

#: Semitone spans of intervals that count as consonant in common-practice harmony.
PERFECT_CONSONANCES = frozenset({0, 7, 12})
IMPERFECT_CONSONANCES = frozenset({3, 4, 8, 9})

#: Semitone span of each simple diatonic interval when perfect/major.
_BASE_SEMITONES: tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11)
#: Which interval numbers are "perfect" rather than "major/minor".
_PERFECT_NUMBERS = frozenset({1, 4, 5, 8})

_QUALITY_NAMES = {
    "P": "perfect",
    "M": "major",
    "m": "minor",
    "A": "augmented",
    "d": "diminished",
    "AA": "doubly augmented",
    "dd": "doubly diminished",
}

_ORDINALS = {
    1: "unison", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "octave",
}  # fmt: skip


@dataclass(frozen=True)
class Interval:
    """A spelled interval between two pitches.

    ``number`` is the diatonic size (3 = a third), ``semitones`` the chromatic size, and
    ``direction`` +1 up / −1 down / 0 unison. Both sizes are needed: C–E and C–Fb are both four
    semitones but only one of them is a third.
    """

    number: int
    semitones: int
    direction: int
    quality: str

    @property
    def simple_number(self) -> int:
        """Interval number reduced within an octave (compound ninths become seconds)."""
        if self.number <= 8:
            return self.number
        reduced = (self.number - 1) % 7 + 1
        return reduced

    @property
    def is_step(self) -> bool:
        return self.number == 2

    @property
    def is_leap(self) -> bool:
        return self.number >= 4

    @property
    def is_augmented_or_diminished(self) -> bool:
        return self.quality in {"A", "d", "AA", "dd"}

    @property
    def abbreviation(self) -> str:
        sign = "-" if self.direction < 0 else ""
        return f"{sign}{self.quality}{self.number}"

    def describe(self) -> str:
        quality = _QUALITY_NAMES.get(self.quality, self.quality)
        ordinal = _ORDINALS.get(self.simple_number, f"{self.simple_number}th")
        compound = "compound " if self.number > 8 else ""
        direction = {1: "up", -1: "down", 0: ""}[self.direction]
        return " ".join(part for part in (compound + quality, ordinal, direction) if part).strip()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.abbreviation


def interval_between(low: Pitch, high: Pitch) -> Interval:
    """The spelled interval from ``low`` to ``high``, signed by direction."""
    diatonic_distance = high.diatonic - low.diatonic
    semitone_distance = high.midi - low.midi
    direction = (
        0
        if diatonic_distance == 0 and semitone_distance == 0
        else (1 if (diatonic_distance or semitone_distance) > 0 else -1)
    )
    steps = abs(diatonic_distance)
    semitones = abs(semitone_distance)
    number = steps + 1

    octaves, remainder = divmod(steps, 7)
    base = _BASE_SEMITONES[remainder] + 12 * octaves
    deviation = semitones - base
    simple_number = remainder + 1

    if simple_number in _PERFECT_NUMBERS or (simple_number == 1 and octaves):
        quality = {0: "P", 1: "A", -1: "d", 2: "AA", -2: "dd"}.get(deviation, "?")
    else:
        quality = {0: "M", -1: "m", 1: "A", -2: "d", 2: "AA", -3: "dd"}.get(deviation, "?")

    return Interval(number=number, semitones=semitones, direction=direction, quality=quality)


def is_consonant(semitones: int) -> bool:
    """Consonance of a *harmonic* interval, reduced to within an octave."""
    reduced = abs(semitones) % 12
    return reduced in PERFECT_CONSONANCES or reduced in IMPERFECT_CONSONANCES


def melodic_difficulty(interval: Interval) -> float:
    """How unlikely this interval is as a melodic move, in ``[0, 1]``.

    Calibrated against how common each interval is in tonal melody rather than against how it
    sounds: steps are free, thirds and perfect consonances are cheap, sevenths and anything
    augmented or diminished are expensive. Used as a prior, never as a verdict — real music is
    full of hard intervals and flagging them all would drown the user.
    """
    if interval.number <= 1:
        return 0.0
    if interval.is_augmented_or_diminished:
        # Augmented seconds are idiomatic in harmonic minor; everything else of this shape is
        # a strong hint that a notehead or an accidental was misread.
        return 0.55 if (interval.simple_number == 2 and interval.quality == "A") else 0.85
    by_number = {2: 0.0, 3: 0.05, 4: 0.15, 5: 0.15, 6: 0.35, 7: 0.7, 8: 0.2}
    base = by_number.get(interval.simple_number, 0.5)
    if interval.number > 8:
        base = min(1.0, base + 0.25 * ((interval.number - 1) // 7))
    return base
