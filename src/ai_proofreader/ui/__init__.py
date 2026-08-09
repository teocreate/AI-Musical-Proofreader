"""The desktop review application.

Importing this package pulls in PySide6. Everything under ``ui.render`` except the Qt widgets is
Qt-free by design, so notation layout can be tested and reused without a GUI installed.
"""

__all__ = ["MainWindow", "build_window", "run"]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """Import Qt lazily so ``import ai_proofreader.ui.render`` works without PySide6."""
    if name in {"run", "build_window"}:
        from .app import build_window, run

        return {"run": run, "build_window": build_window}[name]
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
