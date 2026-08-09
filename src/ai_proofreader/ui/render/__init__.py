"""Notation rendering: pure layout plus thin backends.

``layout`` and ``primitives`` have no Qt dependency and are unit-tested directly; ``svg`` is the
only renderer the application needs, and it serves the review window, HTML reports and
documentation images alike.
"""

from .glyphs import accidental_shapes, clef_shapes, flag_shapes, rest_shapes
from .layout import LayoutEngine, StaffPlacement, layout_score
from .primitives import (
    Anchor,
    Curve,
    Ellipse,
    Line,
    Path,
    Rect,
    RenderedScore,
    RenderedSystem,
    RenderStyle,
    Shape,
    Text,
)
from .svg import DARK_THEME, LIGHT_THEME, SvgTheme, render_svg

__all__ = [
    "DARK_THEME",
    "LIGHT_THEME",
    "Anchor",
    "Curve",
    "Ellipse",
    "LayoutEngine",
    "Line",
    "Path",
    "Rect",
    "RenderStyle",
    "RenderedScore",
    "RenderedSystem",
    "Shape",
    "StaffPlacement",
    "SvgTheme",
    "Text",
    "accidental_shapes",
    "clef_shapes",
    "flag_shapes",
    "layout_score",
    "render_svg",
    "rest_shapes",
]
