"""Left panel: the original scanned page.

Zoomable, scrollable, and — once Phase 2 lands — able to draw the region a suggestion refers to.
The region overlay is written now and simply has nothing to draw while
:attr:`SuggestionTarget.region` is ``None``: the panel says so plainly rather than pretending to
point at something.

PDF rasterization needs PyMuPDF, which is an optional extra. Without it the panel still displays
images and explains what to install, instead of failing to open.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models import SourceRegion

__all__ = ["ScanView"]

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})


class _PageCanvas(QLabel):
    """A page image with an optional highlight rectangle drawn over it."""

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._region: SourceRegion | None = None
        self._scale = 1.0

    def set_region(self, region: SourceRegion | None, scale: float) -> None:
        self._region = region
        self._scale = scale
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def] # noqa: N802 - Qt override
        super().paintEvent(event)
        if self._region is None or self.pixmap() is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ff7a59"))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 122, 89, 40))
        box = self._region.box
        painter.drawRoundedRect(
            QRectF(
                box.x * self._scale,
                box.y * self._scale,
                box.width * self._scale,
                box.height * self._scale,
            ),
            4.0,
            4.0,
        )
        painter.end()


class ScanView(QWidget):
    """Displays the scanned source document beside the recognized notation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages: list[QImage] = []
        self._page_index = 0
        self._zoom = 1.0
        self._region: SourceRegion | None = None

        self._page_picker = QComboBox()
        self._page_picker.currentIndexChanged.connect(self._on_page_changed)
        self._page_picker.setEnabled(False)

        self._status = QLabel("No scan loaded.")
        self._status.setWordWrap(True)

        header = QHBoxLayout()
        header.addWidget(QLabel("Page"))
        header.addWidget(self._page_picker, 1)

        self._canvas = _PageCanvas()
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setWidget(self._canvas)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self._status)
        layout.addWidget(self._scroll, 1)

    # -- loading -------------------------------------------------------------------

    def load(self, path: str | Path) -> bool:
        """Load a scan. Returns ``False`` and explains itself rather than raising."""
        source = Path(path)
        if not source.is_file():
            self._status.setText(f"No such file: {source}")
            return False

        suffix = source.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            image = QImage(str(source))
            if image.isNull():
                self._status.setText(f"{source.name} could not be read as an image.")
                return False
            self._pages = [image]
        elif suffix == ".pdf":
            pages = self._rasterize_pdf(source)
            if pages is None:
                return False
            self._pages = pages
        else:
            self._status.setText(f"{source.name}: expected a PDF, PNG, JPEG or TIFF scan.")
            return False

        self._page_index = 0
        self._page_picker.blockSignals(True)
        self._page_picker.clear()
        self._page_picker.addItems([f"{index + 1}" for index in range(len(self._pages))])
        self._page_picker.setEnabled(len(self._pages) > 1)
        self._page_picker.blockSignals(False)
        self._status.setText(
            f"{source.name} — {len(self._pages)} page(s). "
            "Visual verification arrives in Phase 2; for now this panel is for your own eyes."
        )
        self._render()
        return True

    def _rasterize_pdf(self, source: Path) -> list[QImage] | None:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError:
            self._status.setText(
                "Reading PDF scans needs PyMuPDF. Install it with:\n"
                "    pip install 'ai-musical-proofreader[cv]'\n"
                "or export the pages as PNG and open those."
            )
            return None

        pages: list[QImage] = []
        try:
            with fitz.open(source) as document:
                for page in document:
                    # 200 dpi: enough for staff lines to survive, small enough to stay responsive.
                    pixmap = page.get_pixmap(dpi=200)
                    image = QImage(
                        pixmap.samples,
                        pixmap.width,
                        pixmap.height,
                        pixmap.stride,
                        QImage.Format.Format_RGB888,
                    )
                    pages.append(image.copy())
        except Exception as exc:  # pragma: no cover - depends on the file
            self._status.setText(f"{source.name} could not be rasterized: {exc}")
            return None
        return pages

    # -- display -------------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def set_region(self, region: SourceRegion | None) -> None:
        """Point at a place on the page. Phase 1 never supplies one."""
        self._region = region
        if region is not None and 0 <= region.page < len(self._pages):
            self._page_index = region.page
            self._page_picker.setCurrentIndex(region.page)
        self._render()

    def set_zoom(self, value: float) -> None:
        self._zoom = max(0.1, min(6.0, value))
        self._render()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def fit_width(self) -> None:
        if not self._pages:
            return
        viewport = max(200, self._scroll.viewport().width() - 20)
        self.set_zoom(viewport / self._pages[self._page_index].width())

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def] # noqa: N802 - Qt override
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self._zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15))
            event.accept()
            return
        super().wheelEvent(event)

    def _on_page_changed(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            self._page_index = index
            self._render()

    def _render(self) -> None:
        if not self._pages:
            self._canvas.clear()
            return
        image = self._pages[self._page_index]
        scaled = image.scaled(
            int(image.width() * self._zoom),
            int(image.height() * self._zoom),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._canvas.setPixmap(QPixmap.fromImage(scaled))
        self._canvas.setFixedSize(scaled.size())
        region = (
            self._region
            if self._region is not None and self._region.page == self._page_index
            else None
        )
        self._canvas.set_region(region, self._zoom)
