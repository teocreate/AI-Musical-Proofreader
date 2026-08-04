"""Centre panel: the score as MuseScore recognized it.

Shows a window of measures around whichever suggestion is selected, with the targeted elements
highlighted, so the user can compare the file's reading against the scan on the left without
scrolling anywhere.

Rendering goes through the SVG backend rather than a ``QPainter`` subclass. That keeps every line
of notation-positioning logic in the pure, unit-tested layout module, and means the picture in
this widget is byte-identical to the one in an exported report.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ...models import Score, Suggestion
from ..render import DARK_THEME, LIGHT_THEME, LayoutEngine, RenderStyle, render_svg

__all__ = ["NotationView"]

#: How many measures either side of the selected one to draw.
CONTEXT_MEASURES = 2


class NotationView(QWidget):
    """Renders a window of the parsed score, highlighting a suggestion's target."""

    zoom_changed = Signal(float)

    def __init__(self, dark: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._score: Score | None = None
        self._suggestion: Suggestion | None = None
        self._zoom = 10.0
        self._dark = dark

        self._canvas = QSvgWidget()
        self._canvas.setMinimumSize(400, 200)

        self._placeholder = QLabel("Open a score to see the recognized notation.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setWidget(self._canvas)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._placeholder)
        layout.addWidget(self._scroll)
        self._scroll.hide()

    # -- content -------------------------------------------------------------------

    def set_score(self, score: Score | None) -> None:
        self._score = score
        self._suggestion = None
        self.refresh()

    def set_suggestion(self, suggestion: Suggestion | None) -> None:
        self._suggestion = suggestion
        self.refresh()

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.refresh()

    # -- zoom ----------------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float) -> None:
        self._zoom = max(4.0, min(40.0, value))
        self.zoom_changed.emit(self._zoom)
        self.refresh()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def] # noqa: N802 - Qt override
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self._zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
            event.accept()
            return
        super().wheelEvent(event)

    # -- rendering -----------------------------------------------------------------

    def current_svg(self) -> str:
        """The SVG currently displayed. Exposed so tests can assert on it without a screenshot."""
        if self._score is None or self._score.measure_count == 0:
            return ""
        first, last = self._window()
        engine = LayoutEngine(
            self._score, style=RenderStyle(staff_space=self._zoom), page_width=self._page_width()
        )
        rendered = engine.run(first_measure=first, last_measure=last)
        highlight = set(self._suggestion.target.element_refs) if self._suggestion else set()
        return render_svg(
            rendered, theme=DARK_THEME if self._dark else LIGHT_THEME, highlight_refs=highlight
        )

    def refresh(self) -> None:
        svg = self.current_svg()
        if not svg:
            self._scroll.hide()
            self._placeholder.show()
            return
        self._placeholder.hide()
        self._scroll.show()
        self._canvas.load(svg.encode("utf-8"))
        size = self._canvas.renderer().defaultSize()
        self._canvas.setFixedSize(size)

    def _page_width(self) -> float:
        """Page width in staff spaces, chosen so the music fills the panel at the current zoom."""
        viewport = max(400, self._scroll.viewport().width() - 24)
        return max(60.0, viewport / self._zoom)

    def _window(self) -> tuple[int, int]:
        if self._score is None:
            return (0, 0)
        total = self._score.measure_count
        if self._suggestion is None:
            return (0, min(total - 1, 7))
        centre = self._suggestion.target.measure_index
        first = max(0, centre - CONTEXT_MEASURES)
        last = min(total - 1, centre + CONTEXT_MEASURES)
        return (first, last)
