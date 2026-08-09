"""Application entry point for the review window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..config import DEFAULT_CONFIG, ProofreaderConfig
from .main_window import MainWindow
from .theme import apply_theme

__all__ = ["build_window", "run"]


def build_window(
    score_path: str | Path | None = None,
    scan_path: str | Path | None = None,
    config: ProofreaderConfig | None = None,
) -> MainWindow:
    """Construct the window and load whatever was passed on the command line.

    Separate from :func:`run` so tests can build and drive the window without an event loop.
    """
    window = MainWindow(config or DEFAULT_CONFIG)
    if scan_path:
        window.scan_view.load(scan_path)
    if score_path:
        window.load_score(Path(score_path))
    return window


def run(
    score_path: str | Path | None = None,
    scan_path: str | Path | None = None,
    config: ProofreaderConfig | None = None,
) -> int:
    """Start the review window. Returns the process exit code."""
    app = QApplication.instance() or QApplication(sys.argv)
    settings = config or DEFAULT_CONFIG
    apply_theme(app, dark=settings.ui.dark_mode)
    window = build_window(score_path, scan_path, settings)
    window.show()
    return app.exec()
