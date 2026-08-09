"""Melodic contour and voice-leading plausibility.

The most common OMR pitch error is a notehead read one staff position off. Its fingerprint in a
melody is unmistakable once you look for it: a smooth line acquires a single spike, and moving
that one note back by one step restores the smoothness. This module measures exactly that, and
nothing broader — general "is this melody plausible" scoring is a research project, while
"does this single note stick out of an otherwise smooth line" is a well-posed question.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from ..models import Pitch
from .intervals import interval_between, melodic_difficulty

__all__ = [
    "ContourAnomaly",
    "is_spike_shaped",
    "local_roughness",
    "roughness",
    "spike_analysis",
    "step_alternatives",
]


@dataclass(frozen=True)
class ContourAnomaly:
    """A note that sticks out of its melodic line, and the move that would smooth it."""

    index: int
    #: Diatonic step offset that would smooth the contour (+1 = one staff position up).
    step_offset: int
    roughness_before: float
    roughness_after: float

    @property
    def improvement(self) -> float:
        """How much smoother the line becomes, normalized to ``[0, 1]``."""
        if self.roughness_before <= 0:
            return 0.0
        return max(0.0, (self.roughness_before - self.roughness_after) / self.roughness_before)


def roughness(pitches: list[Pitch] | tuple[Pitch, ...]) -> float:
    """Total melodic difficulty of a line: the sum of its interval costs."""
    if len(pitches) < 2:
        return 0.0
    total = 0.0
    for first, second in itertools.pairwise(pitches):
        total += melodic_difficulty(interval_between(first, second))
    return total


def local_roughness(pitches: list[Pitch] | tuple[Pitch, ...], index: int) -> float:
    """Difficulty of just the intervals touching ``pitches[index]``."""
    total = 0.0
    if index > 0:
        total += melodic_difficulty(interval_between(pitches[index - 1], pitches[index]))
    if index + 1 < len(pitches):
        total += melodic_difficulty(interval_between(pitches[index], pitches[index + 1]))
    return total


def step_alternatives(
    pitch: Pitch, offsets: tuple[int, ...] = (-2, -1, 1, 2)
) -> list[tuple[int, Pitch]]:
    """Candidate misreadings of a notehead, as (offset, pitch) pairs.

    ±1 is a notehead read onto the adjacent line or space — by far the most common OMR slip.
    ±2 covers the line-to-line and space-to-space confusion that happens on skewed or low
    resolution scans. The alteration is dropped to the natural implied by the new letter, because
    the accidental belongs to the staff position and not to the note.
    """
    return [(offset, pitch.step_shifted(offset, alter=0)) for offset in offsets]


def is_spike_shaped(
    previous: Pitch, current: Pitch, following: Pitch, max_return_steps: int = 2
) -> bool:
    """Whether three notes form a spike rather than a leap into a new phrase.

    A misread notehead makes the line jump away and come straight back to roughly where it was.
    A phrase that simply leaps somewhere new — extremely common at bar lines and after rests —
    goes away and *stays* away. Distinguishing the two is what stops this family of rules from
    flagging half the leaps in the repertoire.
    """
    away = current.diatonic - previous.diatonic
    back = following.diatonic - current.diatonic
    if away == 0 or back == 0 or (away > 0) == (back > 0):
        return False
    return abs(following.diatonic - previous.diatonic) <= max_return_steps


def spike_analysis(
    pitches: list[Pitch] | tuple[Pitch, ...],
    index: int,
    offsets: tuple[int, ...] = (-2, -1, 1, 2),
    minimum_local_roughness: float = 0.35,
    max_return_steps: int = 2,
) -> ContourAnomaly | None:
    """Test whether ``pitches[index]`` is a contour spike with an obvious repair.

    Returns ``None`` unless the note has the spike *shape*, is genuinely rough, and a
    single-step move makes it markedly smoother. All three conditions matter: without the shape
    test every phrase-opening leap is a finding, without the roughness test every ordinary
    neighbour tone is, and without the improvement test we would flag notes we cannot fix.
    """
    if index <= 0 or index + 1 >= len(pitches):
        return None  # endpoints have no contour to stick out of

    if not is_spike_shaped(
        pitches[index - 1], pitches[index], pitches[index + 1], max_return_steps
    ):
        return None

    before = local_roughness(pitches, index)
    if before < minimum_local_roughness:
        return None

    best: ContourAnomaly | None = None
    for offset, candidate in step_alternatives(pitches[index], offsets):
        trial = list(pitches)
        trial[index] = candidate
        after = local_roughness(trial, index)
        if after >= before:
            continue
        anomaly = ContourAnomaly(
            index=index, step_offset=offset, roughness_before=before, roughness_after=after
        )
        if best is None or anomaly.improvement > best.improvement:
            best = anomaly
    return best
