"""SVG backend.

Turns a :class:`RenderedScore` into standalone SVG. Used by the review window (displayed through
``QSvgWidget``), by HTML reports, and by documentation images — one layout, three destinations.

Rendering to SVG rather than painting directly with ``QPainter`` is a deliberate simplification:
it means the notation view has no Qt-specific drawing code to test, the same picture appears in a
report and in the window, and zooming is a re-render rather than a transform stack.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import (
    Anchor,
    Curve,
    Ellipse,
    Line,
    Path,
    Rect,
    RenderedScore,
    Shape,
    Text,
)

__all__ = ["DARK_THEME", "LIGHT_THEME", "SvgTheme", "render_svg"]


class SvgTheme:
    """Colours for one appearance mode."""

    def __init__(
        self,
        ink: str = "#1a1a1a",
        paper: str = "#ffffff",
        label: str = "#666666",
        highlight: str = "#d94f2b",
        highlight_fill: str = "#d94f2b22",
        secondary: str = "#3a7bd5",
    ) -> None:
        self.ink = ink
        self.paper = paper
        self.label = label
        self.highlight = highlight
        self.highlight_fill = highlight_fill
        self.secondary = secondary


LIGHT_THEME = SvgTheme()
DARK_THEME = SvgTheme(
    ink="#e8e8e8",
    paper="#1e1f22",
    label="#9aa0a6",
    highlight="#ff7a59",
    highlight_fill="#ff7a5933",
    secondary="#6fa8ff",
)


def render_svg(
    rendered: RenderedScore,
    theme: SvgTheme | None = None,
    highlight_refs: set[int] | None = None,
    scale: float | None = None,
) -> str:
    """Render to a standalone SVG document.

    ``highlight_refs`` draws an attention box around the elements a suggestion targets and inks
    them in the highlight colour — the whole reason shapes carry their element handle.
    """
    palette = theme or LIGHT_THEME
    highlighted = highlight_refs or set()
    unit = scale if scale is not None else rendered.style.staff_space

    width = rendered.width * unit
    height = rendered.height * unit
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height:.1f}" '
        f'viewBox="0 0 {width:.1f} {height:.1f}">',
        f'<rect width="100%" height="100%" fill="{palette.paper}"/>',
        f'<g transform="scale({unit})">',
    ]

    for ref in sorted(highlighted):
        region = rendered.region_for(ref)
        if region is None:
            continue
        x, y, box_width, box_height = region
        parts.append(
            f'<rect x="{x - 0.35:.3f}" y="{y - 0.35:.3f}" width="{box_width + 0.7:.3f}" '
            f'height="{box_height + 0.7:.3f}" rx="0.3" fill="{palette.highlight_fill}" '
            f'stroke="{palette.highlight}" stroke-width="0.1"/>'
        )

    for shape in rendered.shapes:
        colour = palette.highlight if shape.ref in highlighted else _colour_for(shape, palette)
        fragment = _render_shape(shape, colour, unit)
        if fragment:
            parts.append(fragment)

    parts.append("</g></svg>")
    return "".join(parts)


def _colour_for(shape: Shape, palette: SvgTheme) -> str:
    if shape.role == "label":
        return palette.label
    if shape.role in {"staff", "ledger"}:
        return palette.ink
    return palette.ink


def _render_shape(shape: Shape, colour: str, unit: float) -> str:
    if isinstance(shape, Line):
        return (
            f'<line x1="{shape.x1:.3f}" y1="{shape.y1:.3f}" x2="{shape.x2:.3f}" '
            f'y2="{shape.y2:.3f}" stroke="{colour}" stroke-width="{shape.width:.3f}" '
            f'stroke-linecap="round"/>'
        )
    if isinstance(shape, Ellipse):
        fill = colour if shape.filled else "none"
        stroke = "none" if shape.filled else colour
        transform = (
            f' transform="rotate({shape.rotation:.1f} {shape.cx:.3f} {shape.cy:.3f})"'
            if shape.rotation
            else ""
        )
        return (
            f'<ellipse cx="{shape.cx:.3f}" cy="{shape.cy:.3f}" rx="{shape.rx:.3f}" '
            f'ry="{shape.ry:.3f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{shape.width:.3f}"{transform}/>'
        )
    if isinstance(shape, Rect):
        fill = colour if shape.filled else "none"
        return (
            f'<rect x="{shape.x:.3f}" y="{shape.y:.3f}" width="{shape.width_:.3f}" '
            f'height="{shape.height:.3f}" fill="{fill}"/>'
        )
    if isinstance(shape, Path):
        fill = colour if shape.filled else "none"
        stroke = "none" if shape.filled else colour
        return (
            f'<path d="{shape.commands}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{shape.width:.3f}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    if isinstance(shape, Curve):
        middle_x = (shape.x1 + shape.x2) / 2
        middle_y = (shape.y1 + shape.y2) / 2 + shape.bulge
        return (
            f'<path d="M {shape.x1:.3f} {shape.y1:.3f} Q {middle_x:.3f} {middle_y:.3f} '
            f'{shape.x2:.3f} {shape.y2:.3f}" fill="none" stroke="{colour}" '
            f'stroke-width="{shape.width:.3f}" stroke-linecap="round"/>'
        )
    if isinstance(shape, Text):
        anchor = {Anchor.START: "start", Anchor.MIDDLE: "middle", Anchor.END: "end"}[shape.anchor]
        weight = ' font-weight="bold"' if shape.bold else ""
        style = ' font-style="italic"' if shape.italic else ""
        return (
            f'<text x="{shape.x:.3f}" y="{shape.y:.3f}" font-size="{shape.size:.3f}" '
            f'fill="{colour}" text-anchor="{anchor}" '
            f"font-family=\"Georgia, 'Times New Roman', serif\"{weight}{style}>"
            f"{escape(shape.text)}</text>"
        )
    return ""
