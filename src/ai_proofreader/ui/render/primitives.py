"""Drawing primitives and engraving style.

The renderer is split in two on purpose: this module and :mod:`layout` are pure Python that
produce a list of shapes, and the backends (:mod:`svg`, and Qt in the widgets) only know how to
draw those shapes. Notation-layout bugs are then found by pytest rather than by squinting at a
screenshot, and the same layout can be rendered into a report, a widget, or a documentation image
without duplicating a line of positioning logic.

Everything is measured in **staff spaces** during layout and converted to pixels once, at the
end. That is how engraving actually works — notehead width, stem length and accidental size are
all fixed multiples of the space between staff lines — and it makes the output resolution
independent for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Anchor",
    "Curve",
    "Ellipse",
    "Line",
    "Path",
    "Rect",
    "RenderStyle",
    "RenderedScore",
    "RenderedSystem",
    "Shape",
    "Text",
]


class Anchor(StrEnum):
    START = "start"
    MIDDLE = "middle"
    END = "end"


@dataclass(frozen=True)
class RenderStyle:
    """Engraving proportions, in staff spaces except where noted."""

    #: Pixels per staff space. The single knob that controls output size.
    staff_space: float = 8.0

    staff_line_width: float = 0.12
    stem_width: float = 0.13
    barline_width: float = 0.16
    heavy_barline_width: float = 0.5

    notehead_width: float = 1.18
    notehead_height: float = 1.0
    #: Noteheads are drawn as ellipses rotated off the horizontal, as engraved ones are.
    notehead_angle: float = -20.0
    stem_length: float = 3.5
    dot_radius: float = 0.16
    ledger_extension: float = 0.35

    staff_gap: float = 8.0
    system_gap: float = 5.0
    margin_x: float = 3.0
    margin_y: float = 3.0

    #: Horizontal space reserved for clef, key signature and time signature.
    clef_width: float = 3.2
    key_accidental_width: float = 1.1
    time_width: float = 2.6

    min_measure_width: float = 12.0
    #: Extra width granted per event in a measure, so busy bars get more room.
    width_per_event: float = 2.6
    note_padding: float = 1.4

    label_width: float = 9.0
    font_size: float = 1.9
    label_font_size: float = 1.5

    def to_pixels(self, spaces: float) -> float:
        return spaces * self.staff_space


@dataclass(frozen=True)
class Shape:
    """Base class for everything the backends know how to draw."""

    #: Element handle this shape belongs to, so the UI can highlight by suggestion target.
    ref: int = -1
    #: Free-form role, used by backends for styling ("staff", "stem", "notehead" …).
    role: str = ""


@dataclass(frozen=True)
class Line(Shape):
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    width: float = 0.1


@dataclass(frozen=True)
class Ellipse(Shape):
    cx: float = 0.0
    cy: float = 0.0
    rx: float = 0.5
    ry: float = 0.5
    rotation: float = 0.0
    filled: bool = True
    width: float = 0.12


@dataclass(frozen=True)
class Rect(Shape):
    x: float = 0.0
    y: float = 0.0
    width_: float = 1.0
    height: float = 1.0
    filled: bool = True


@dataclass(frozen=True)
class Path(Shape):
    """A filled or stroked path. ``commands`` uses SVG path syntax in staff spaces."""

    commands: str = ""
    filled: bool = True
    width: float = 0.12


@dataclass(frozen=True)
class Curve(Shape):
    """A slur or tie: a quadratic arc from one point to another."""

    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    bulge: float = 1.0
    width: float = 0.12


@dataclass(frozen=True)
class Text(Shape):
    x: float = 0.0
    y: float = 0.0
    text: str = ""
    size: float = 1.8
    anchor: Anchor = Anchor.START
    bold: bool = False
    italic: bool = False


@dataclass
class RenderedSystem:
    """One horizontal band of music: every part, for one range of measures."""

    index: int
    top: float
    height: float
    first_measure: int
    last_measure: int
    shapes: list[Shape] = field(default_factory=list)


@dataclass
class RenderedScore:
    """A complete laid-out score, in staff spaces, ready for any backend."""

    width: float
    height: float
    style: RenderStyle
    systems: list[RenderedSystem] = field(default_factory=list)
    #: Element handle → bounding box (x, y, width, height), for highlighting and scrolling.
    regions: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)
    #: Measure index → x range, for locating a suggestion that has no element handle.
    measure_spans: dict[int, tuple[float, float]] = field(default_factory=dict)

    @property
    def shapes(self) -> list[Shape]:
        return [shape for system in self.systems for shape in system.shapes]

    def region_for(self, ref: int) -> tuple[float, float, float, float] | None:
        return self.regions.get(ref)

    def system_containing(self, measure_index: int) -> RenderedSystem | None:
        for system in self.systems:
            if system.first_measure <= measure_index <= system.last_measure:
                return system
        return None
