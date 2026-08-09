"""Glyph construction: clefs, accidentals, rests and flags.

These are drawn as vector paths rather than set in a music font. The reason is deployment: SMuFL
fonts are a licensing and bundling problem, and a missing font renders a score as a row of
tofu boxes — the worst possible failure for a tool whose entire job is showing someone what a
symbol looks like. Drawn glyphs are always available, always positioned correctly, and good
enough to answer the only question this panel exists to answer: *is the note where the file says
it is?*

Swapping in Bravura behind the same interface is a Phase 4 item; every function here returns
shapes in staff spaces relative to an origin, so the substitution is local.

All coordinates are in staff spaces, y increasing downward, origin at the given anchor point.
"""

from __future__ import annotations

from .primitives import Ellipse, Line, Path, Rect, Shape

__all__ = [
    "accidental_shapes",
    "alto_clef",
    "bass_clef",
    "clef_shapes",
    "double_sharp",
    "flag_shapes",
    "flat",
    "natural",
    "rest_shapes",
    "sharp",
    "treble_clef",
]


def _path(
    commands: str, x: float, y: float, ref: int, role: str, filled: bool = True, width: float = 0.12
) -> Path:
    """Translate an SVG path fragment written around the origin to (x, y)."""
    return Path(ref=ref, role=role, commands=f"M {x} {y} " + commands, filled=filled, width=width)


def treble_clef(x: float, y_g4: float, ref: int = -1) -> list[Shape]:
    """A G clef, anchored so the spiral centre sits on the G4 line."""
    shapes: list[Shape] = [
        # Descending tail and the loop below the staff.
        Path(
            ref=ref,
            role="clef",
            commands=(
                f"M {x + 0.35} {y_g4 - 3.4} "
                f"C {x - 0.9} {y_g4 - 2.4} {x - 0.95} {y_g4 - 0.7} {x + 0.2} {y_g4 - 0.2} "
                f"C {x + 1.3} {y_g4 + 0.3} {x + 1.45} {y_g4 + 1.6} {x + 0.55} {y_g4 + 2.1} "
                f"C {x - 0.2} {y_g4 + 2.5} {x - 0.9} {y_g4 + 2.0} {x - 0.75} {y_g4 + 1.3}"
            ),
            filled=False,
            width=0.22,
        ),
        Path(
            ref=ref,
            role="clef",
            commands=(
                f"M {x + 0.35} {y_g4 - 3.4} "
                f"C {x + 1.1} {y_g4 - 2.3} {x + 0.9} {y_g4 - 0.4} {x + 0.3} {y_g4 + 1.4} "
                f"C {x - 0.05} {y_g4 + 2.5} {x - 0.1} {y_g4 + 3.4} {x + 0.35} {y_g4 + 3.6}"
            ),
            filled=False,
            width=0.22,
        ),
        # The spiral around the G line.
        Ellipse(
            ref=ref, role="clef", cx=x + 0.05, cy=y_g4, rx=0.62, ry=0.55, filled=False, width=0.22
        ),
        Ellipse(ref=ref, role="clef", cx=x + 0.05, cy=y_g4, rx=0.2, ry=0.18, filled=True),
    ]
    return shapes


def bass_clef(x: float, y_f3: float, ref: int = -1) -> list[Shape]:
    """An F clef, anchored on the F3 line."""
    return [
        Path(
            ref=ref,
            role="clef",
            commands=(
                f"M {x + 1.5} {y_f3 - 0.55} "
                f"C {x + 1.5} {y_f3 - 1.5} {x - 0.2} {y_f3 - 1.6} {x - 0.35} {y_f3 - 0.3} "
                f"C {x + 0.6} {y_f3 - 1.0} {x + 1.0} {y_f3 - 0.2} {x + 0.85} {y_f3 + 0.8} "
                f"C {x + 0.7} {y_f3 + 1.8} {x - 0.1} {y_f3 + 2.4} {x - 0.9} {y_f3 + 2.6}"
            ),
            filled=False,
            width=0.3,
        ),
        Ellipse(ref=ref, role="clef", cx=x + 1.25, cy=y_f3 - 0.55, rx=0.22, ry=0.22, filled=True),
        Ellipse(ref=ref, role="clef", cx=x + 2.0, cy=y_f3 - 0.5, rx=0.16, ry=0.16, filled=True),
        Ellipse(ref=ref, role="clef", cx=x + 2.0, cy=y_f3 + 0.5, rx=0.16, ry=0.16, filled=True),
    ]


def alto_clef(x: float, y_c4: float, ref: int = -1) -> list[Shape]:
    """A C clef, anchored on the line it names."""
    return [
        Rect(ref=ref, role="clef", x=x, y=y_c4 - 2.0, width_=0.28, height=4.0, filled=True),
        Rect(ref=ref, role="clef", x=x + 0.45, y=y_c4 - 2.0, width_=0.16, height=4.0, filled=True),
        Path(
            ref=ref,
            role="clef",
            commands=(
                f"M {x + 0.7} {y_c4 - 2.0} "
                f"C {x + 1.9} {y_c4 - 2.0} {x + 1.9} {y_c4 - 0.2} {x + 0.85} {y_c4 - 0.05} "
                f"C {x + 1.9} {y_c4 + 0.1} {x + 1.9} {y_c4 + 2.0} {x + 0.7} {y_c4 + 2.0}"
            ),
            filled=False,
            width=0.26,
        ),
    ]


def clef_shapes(sign: str, x: float, y_reference: float, ref: int = -1) -> list[Shape]:
    """Draw whichever clef ``sign`` names, anchored on its reference line."""
    if sign == "F":
        return bass_clef(x, y_reference, ref)
    if sign == "C":
        return alto_clef(x, y_reference, ref)
    if sign in {"percussion", "TAB", "none"}:
        return [
            Rect(ref=ref, role="clef", x=x + 0.2, y=y_reference - 1.0, width_=0.3, height=2.0),
            Rect(ref=ref, role="clef", x=x + 0.9, y=y_reference - 1.0, width_=0.3, height=2.0),
        ]
    return treble_clef(x, y_reference, ref)


def sharp(x: float, y: float, ref: int = -1) -> list[Shape]:
    """Two uprights crossed by two thick, slightly rising bars."""
    return [
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.18,
            y1=y - 1.0,
            x2=x + 0.18,
            y2=y + 0.85,
            width=0.12,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.62,
            y1=y - 1.15,
            x2=x + 0.62,
            y2=y + 0.7,
            width=0.12,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x - 0.05,
            y1=y - 0.18,
            x2=x + 0.85,
            y2=y - 0.42,
            width=0.26,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x - 0.05,
            y1=y + 0.52,
            x2=x + 0.85,
            y2=y + 0.28,
            width=0.26,
        ),
    ]


def flat(x: float, y: float, ref: int = -1) -> list[Shape]:
    """An upright with a bowl to its right, sitting on the note's line."""
    return [
        Line(
            ref=ref, role="accidental", x1=x + 0.15, y1=y - 1.6, x2=x + 0.15, y2=y + 0.5, width=0.13
        ),
        Path(
            ref=ref,
            role="accidental",
            commands=(
                f"M {x + 0.15} {y + 0.5} "
                f"C {x + 0.9} {y - 0.2} {x + 0.95} {y - 0.9} {x + 0.15} {y - 0.55}"
            ),
            filled=False,
            width=0.2,
        ),
    ]


def natural(x: float, y: float, ref: int = -1) -> list[Shape]:
    return [
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.12,
            y1=y - 1.1,
            x2=x + 0.12,
            y2=y + 0.55,
            width=0.12,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.62,
            y1=y - 0.55,
            x2=x + 0.62,
            y2=y + 1.1,
            width=0.12,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.12,
            y1=y - 0.28,
            x2=x + 0.62,
            y2=y - 0.45,
            width=0.22,
        ),
        Line(
            ref=ref,
            role="accidental",
            x1=x + 0.12,
            y1=y + 0.42,
            x2=x + 0.62,
            y2=y + 0.25,
            width=0.22,
        ),
    ]


def double_sharp(x: float, y: float, ref: int = -1) -> list[Shape]:
    return [
        Line(ref=ref, role="accidental", x1=x, y1=y - 0.35, x2=x + 0.7, y2=y + 0.35, width=0.22),
        Line(ref=ref, role="accidental", x1=x, y1=y + 0.35, x2=x + 0.7, y2=y - 0.35, width=0.22),
    ]


def accidental_shapes(name: str, x: float, y: float, ref: int = -1) -> list[Shape]:
    """Draw an accidental by its MusicXML name."""
    if name == "flat":
        return flat(x, y, ref)
    if name == "natural":
        return natural(x, y, ref)
    if name == "double-sharp":
        return double_sharp(x, y, ref)
    if name == "flat-flat":
        return flat(x, y, ref) + flat(x + 0.85, y, ref)
    return sharp(x, y, ref)


def rest_shapes(note_type: str | None, x: float, y_middle: float, ref: int = -1) -> list[Shape]:
    """A rest of the given written value, positioned around the middle staff line."""
    kind = note_type or "quarter"
    if kind in {"whole", "breve"}:
        # Hangs from the fourth line.
        return [Rect(ref=ref, role="rest", x=x, y=y_middle - 1.0, width_=1.1, height=0.45)]
    if kind == "half":
        # Sits on the third line.
        return [Rect(ref=ref, role="rest", x=x, y=y_middle, width_=1.1, height=0.45)]
    if kind == "quarter":
        return [
            Path(
                ref=ref,
                role="rest",
                commands=(
                    f"M {x + 0.15} {y_middle - 1.4} "
                    f"L {x + 0.75} {y_middle - 0.45} L {x + 0.15} {y_middle + 0.2} "
                    f"L {x + 0.8} {y_middle + 1.1} "
                    f"C {x + 0.35} {y_middle + 0.75} {x + 0.05} {y_middle + 1.05} "
                    f"{x + 0.45} {y_middle + 1.6}"
                ),
                filled=False,
                width=0.22,
            )
        ]

    hooks = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4, "128th": 5}.get(kind, 1)
    shapes: list[Shape] = [
        Line(
            ref=ref,
            role="rest",
            x1=x + 0.75,
            y1=y_middle - 0.9 - 0.55 * (hooks - 1),
            x2=x + 0.25,
            y2=y_middle + 1.0,
            width=0.14,
        )
    ]
    for index in range(hooks):
        top = y_middle - 0.75 - 0.55 * index
        shapes.append(Ellipse(ref=ref, role="rest", cx=x + 0.72, cy=top, rx=0.2, ry=0.18))
        shapes.append(
            Path(
                ref=ref,
                role="rest",
                commands=(
                    f"M {x + 0.72} {top} C {x + 0.35} {top - 0.15} "
                    f"{x + 0.2} {top + 0.2} {x + 0.15} {top + 0.35}"
                ),
                filled=False,
                width=0.14,
            )
        )
    return shapes


def flag_shapes(count: int, x: float, y: float, upward: bool, ref: int = -1) -> list[Shape]:
    """Flags on a stem end. ``count`` is one for an eighth, two for a sixteenth, and so on."""
    shapes: list[Shape] = []
    direction = 1 if upward else -1
    for index in range(count):
        offset = index * 0.75 * direction
        shapes.append(
            Path(
                ref=ref,
                role="flag",
                commands=(
                    f"M {x} {y + offset} "
                    f"C {x + 1.0} {y + offset + 0.7 * direction} "
                    f"{x + 1.1} {y + offset + 1.5 * direction} "
                    f"{x + 0.35} {y + offset + 2.2 * direction}"
                ),
                filled=False,
                width=0.2,
            )
        )
    return shapes
