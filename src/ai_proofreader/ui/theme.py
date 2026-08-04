"""Application palette.

A dark default, because proofreading is done for hours at a time and the two things that must
stand out — the scan and the notation — are both mostly white.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

__all__ = ["DARK_PALETTE", "LIGHT_PALETTE", "apply_theme"]

DARK_PALETTE = {
    QPalette.ColorRole.Window: "#232529",
    QPalette.ColorRole.WindowText: "#e6e6e6",
    QPalette.ColorRole.Base: "#1b1d21",
    QPalette.ColorRole.AlternateBase: "#232529",
    QPalette.ColorRole.Text: "#e6e6e6",
    QPalette.ColorRole.Button: "#2b2e33",
    QPalette.ColorRole.ButtonText: "#e6e6e6",
    QPalette.ColorRole.Highlight: "#3d6fd6",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.ToolTipBase: "#2b2e33",
    QPalette.ColorRole.ToolTipText: "#e6e6e6",
}

LIGHT_PALETTE = {
    QPalette.ColorRole.Window: "#f4f4f6",
    QPalette.ColorRole.WindowText: "#1a1a1a",
    QPalette.ColorRole.Base: "#ffffff",
    QPalette.ColorRole.AlternateBase: "#f0f0f2",
    QPalette.ColorRole.Text: "#1a1a1a",
    QPalette.ColorRole.Button: "#e8e8ea",
    QPalette.ColorRole.ButtonText: "#1a1a1a",
    QPalette.ColorRole.Highlight: "#3d6fd6",
    QPalette.ColorRole.HighlightedText: "#ffffff",
}


def apply_theme(app: QApplication, dark: bool = True) -> None:
    """Apply the palette to a running application."""
    palette = QPalette()
    for role, colour in (DARK_PALETTE if dark else LIGHT_PALETTE).items():
        palette.setColor(role, QColor(colour))
    app.setPalette(palette)
    app.setStyle("Fusion")
