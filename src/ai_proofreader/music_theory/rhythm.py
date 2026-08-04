"""Notatable durations.

Rhythm errors from OMR are almost always *notational*: a dot that was not seen, a flag counted
wrong, a tuplet bracket missed. Detecting them means knowing which durations can be written at
all, and which small edit turns the written duration into the one the bar needs.
"""

from __future__ import annotations

from fractions import Fraction

__all__ = [
    "TUPLET_RATIOS",
    "TYPE_LENGTHS",
    "describe_quarters",
    "dot_variants",
    "duration_to_type",
    "is_notatable",
    "scale_variants",
    "try_duration_to_type",
    "type_length",
]

#: Quarter-note length of each un-dotted note type, longest first.
TYPE_LENGTHS: tuple[tuple[str, Fraction], ...] = (
    ("breve", Fraction(8)),
    ("whole", Fraction(4)),
    ("half", Fraction(2)),
    ("quarter", Fraction(1)),
    ("eighth", Fraction(1, 2)),
    ("16th", Fraction(1, 4)),
    ("32nd", Fraction(1, 8)),
    ("64th", Fraction(1, 16)),
    ("128th", Fraction(1, 32)),
)

#: Tuplet ratios (actual : normal) common enough to infer without a bracket in the file.
TUPLET_RATIOS: tuple[tuple[int, int], ...] = (
    (3, 2),
    (5, 4),
    (6, 4),
    (7, 4),
    (7, 8),
    (9, 8),
    (2, 3),
    (4, 3),
)

_TYPE_BY_NAME = dict(TYPE_LENGTHS)


def _dot_multiplier(dots: int) -> Fraction:
    """``1``, ``3/2``, ``7/4``, ``15/8`` …"""
    return Fraction(2 ** (dots + 1) - 1, 2**dots)


def type_length(note_type: str, dots: int = 0) -> Fraction | None:
    """Quarter-note length of a written note type, or ``None`` if unknown."""
    base = _TYPE_BY_NAME.get(note_type)
    if base is None:
        return None
    return base * _dot_multiplier(dots)


def try_duration_to_type(
    quarters: Fraction, allow_tuplets: bool = True
) -> tuple[str, int, tuple[int, int] | None] | None:
    """Best ``(note_type, dots, tuplet)`` for a duration, or ``None`` if unwritable."""
    if quarters <= 0:
        return None
    for dots in (0, 1, 2, 3):
        multiplier = _dot_multiplier(dots)
        for name, base in TYPE_LENGTHS:
            if base * multiplier == quarters:
                return name, dots, None
    if not allow_tuplets:
        return None
    for actual, normal in TUPLET_RATIOS:
        written = quarters * actual / normal
        for dots in (0, 1):
            multiplier = _dot_multiplier(dots)
            for name, base in TYPE_LENGTHS:
                if base * multiplier == written:
                    return name, dots, (actual, normal)
    return None


def duration_to_type(quarters: Fraction) -> tuple[str, int, tuple[int, int] | None]:
    """Like :func:`try_duration_to_type` but raising instead of returning ``None``."""
    result = try_duration_to_type(quarters)
    if result is None:
        raise ValueError(f"cannot notate a duration of {quarters} quarter notes")
    return result


def is_notatable(quarters: Fraction, allow_tuplets: bool = True) -> bool:
    return try_duration_to_type(quarters, allow_tuplets) is not None


def dot_variants(quarters: Fraction, current_dots: int) -> list[tuple[int, Fraction]]:
    """Every dotting of the same base note value, as ``(dots, quarter_length)``.

    Adding or removing a dot is the single most common rhythm repair, so the detector needs the
    exact durations that dotting would produce rather than a guess.
    """
    multiplier = _dot_multiplier(current_dots)
    base = quarters / multiplier
    variants: list[tuple[int, Fraction]] = []
    for dots in (0, 1, 2, 3):
        if dots == current_dots:
            continue
        variants.append((dots, base * _dot_multiplier(dots)))
    return variants


def scale_variants(quarters: Fraction) -> list[Fraction]:
    """Durations one note value away in either direction — the flag-miscount repair."""
    return [quarters * 2, quarters / 2]


def describe_quarters(quarters: Fraction) -> str:
    """Readable duration, preferring the written name."""
    guess = try_duration_to_type(quarters)
    if guess is None:
        return f"{quarters} quarter notes"
    name, dots, tuplet = guess
    prefix = {0: "", 1: "dotted ", 2: "double-dotted ", 3: "triple-dotted "}.get(dots, "")
    suffix = f" ({tuplet[0]}:{tuplet[1]})" if tuplet else ""
    return f"{prefix}{name}{suffix}"
