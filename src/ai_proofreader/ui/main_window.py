"""The review window.

Three panels, left to right: the scan, the recognized notation, the suggestion queue. The
workflow is a spell checker's — walk the queue, glance between the two views, press one key.

Two behaviours here are load-bearing rather than cosmetic:

* **Analysis runs off the UI thread.** A hundred-page score takes seconds; a frozen window during
  those seconds makes the tool feel broken.
* **Accepting is applied immediately but never saved automatically.** The edit goes into the
  in-memory document so the notation panel can show the result, and the file on disk is untouched
  until the user explicitly exports. Nothing is ever silently modified.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from ..config import DEFAULT_CONFIG, ProofreaderConfig
from ..edits import EditApplier
from ..models import Score, Suggestion, SuggestionStatus
from ..pipeline import Proofreader, write_report
from ..score_parser import ProofreaderError, SourceDocument, parse_score
from .widgets.notation_view import NotationView
from .widgets.scan_view import ScanView
from .widgets.suggestion_panel import SuggestionPanel

__all__ = ["AnalysisWorker", "MainWindow"]

logger = logging.getLogger(__name__)

SCORE_FILTER = "Scores (*.musicxml *.xml *.mxl *.mscz);;All files (*)"
SCAN_FILTER = "Scans (*.pdf *.png *.jpg *.jpeg *.tif *.tiff);;All files (*)"


class AnalysisWorker(QObject):
    """Runs an analysis on a background thread."""

    finished = Signal(object, object, object)  # score, document, report
    failed = Signal(str)

    def __init__(self, path: Path, config: ProofreaderConfig) -> None:
        super().__init__()
        self._path = path
        self._config = config

    def run(self) -> None:
        try:
            document = SourceDocument.load(self._path)
            score = parse_score(document)
            result = Proofreader(self._config).analyze(score, document)
        except ProofreaderError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("analysis failed")
            self.failed.emit(f"Unexpected failure: {exc}")
            return
        self.finished.emit(score, document, result.report)


class MainWindow(QMainWindow):
    """The three-panel review window."""

    def __init__(self, config: ProofreaderConfig | None = None) -> None:
        super().__init__()
        self.config = config or DEFAULT_CONFIG
        self.score: Score | None = None
        self.document: SourceDocument | None = None
        self.applier: EditApplier | None = None
        self._score_path: Path | None = None
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._needs_reanalysis = False

        # User-interaction seams. Modal dialogs are hostile to both testing and future
        # preferences ("don't ask again"), so the policy lives behind a callable rather than
        # inside the handlers.
        self.confirm_discard: Callable[[int], bool] = self._ask_discard
        self.notify: Callable[[str, str], None] = self._show_message

        self.setWindowTitle("AI Musical Proofreader")
        self.resize(1500, 900)

        self.scan_view = ScanView()
        self.notation_view = NotationView(dark=self.config.ui.dark_mode)
        self.suggestion_panel = SuggestionPanel(review_threshold=self.config.ui.review_threshold)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.scan_view)
        splitter.addWidget(self.notation_view)
        splitter.addWidget(self.suggestion_panel)
        splitter.setSizes([450, 620, 430])
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open a score to begin.")

        self._build_menu()
        self._connect()

    # -- construction --------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._add_action(file_menu, "&Open score…", QKeySequence.StandardKey.Open, self.open_score)
        self._add_action(file_menu, "Open &scan…", "Ctrl+Shift+O", self.open_scan)
        file_menu.addSeparator()
        self._add_action(
            file_menu, "&Export corrected score…", QKeySequence.StandardKey.Save, self.export_score
        )
        self._add_action(file_menu, "Export &report…", "Ctrl+Shift+S", self.export_report)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Quit", QKeySequence.StandardKey.Quit, self.close)

        review = self.menuBar().addMenu("&Review")
        self._add_action(review, "&Accept", "A", self._accept_current)
        self._add_action(review, "&Reject", "R", self._reject_current)
        self._add_action(review, "&Ignore", "I", self._ignore_current)
        review.addSeparator()
        self._add_action(review, "&Next issue", "N", self.suggestion_panel.select_next)
        self._add_action(review, "&Previous issue", "P", self.suggestion_panel.select_previous)
        self._add_action(
            review, "Next &pending", "Shift+N", self.suggestion_panel.select_next_pending
        )
        review.addSeparator()
        self._add_action(review, "Re-&analyse", "F5", self.reanalyse)
        self._add_action(review, "&Undo last correction", QKeySequence.StandardKey.Undo, self.undo)

        view = self.menuBar().addMenu("&View")
        self._add_action(
            view, "Zoom &in (notation)", QKeySequence.StandardKey.ZoomIn, self.notation_view.zoom_in
        )
        self._add_action(
            view,
            "Zoom &out (notation)",
            QKeySequence.StandardKey.ZoomOut,
            self.notation_view.zoom_out,
        )
        self._add_action(view, "Zoom in (&scan)", "Ctrl+Alt+=", self.scan_view.zoom_in)
        self._add_action(view, "Zoom out (s&can)", "Ctrl+Alt+-", self.scan_view.zoom_out)
        self._add_action(view, "&Fit scan width", "Ctrl+0", self.scan_view.fit_width)

    def _add_action(self, menu, title: str, shortcut, slot) -> QAction:  # type: ignore[no-untyped-def]
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _connect(self) -> None:
        self.suggestion_panel.selection_changed.connect(self._on_selection)
        self.suggestion_panel.accepted.connect(self.accept_suggestion)
        self.suggestion_panel.rejected.connect(
            lambda suggestion: self._set_status(suggestion, SuggestionStatus.REJECTED)
        )
        self.suggestion_panel.ignored.connect(
            lambda suggestion: self._set_status(suggestion, SuggestionStatus.IGNORED)
        )

    # -- file handling -------------------------------------------------------------

    def open_score(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open score", "", SCORE_FILTER)
        if path:
            self.load_score(Path(path))

    def open_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open scan", "", SCAN_FILTER)
        if path:
            self.scan_view.load(path)

    def load_score(self, path: Path) -> None:
        self._score_path = path
        self.statusBar().showMessage(f"Analysing {path.name}…")
        self._start_analysis(path)

    def _start_analysis(self, path: Path) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        thread = QThread(self)
        worker = AnalysisWorker(path, self.config)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_analysis_finished)
        worker.failed.connect(self._on_analysis_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: setattr(self, "_thread", None))
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_analysis_finished(self, score, document, report) -> None:  # type: ignore[no-untyped-def]
        self.score = score
        self.document = document
        self.applier = EditApplier(document)
        self._needs_reanalysis = False
        self.notation_view.set_score(score)
        self.suggestion_panel.set_suggestions(list(report.suggestions))
        self.statusBar().showMessage(
            f"{report.score_summary} — {len(report.suggestions)} suggestion(s) "
            f"in {report.total_ms:.0f} ms"
        )

    def _on_analysis_failed(self, message: str) -> None:
        self.notify("Could not analyse score", message)
        self.statusBar().showMessage("Analysis failed.")

    def reanalyse(self) -> None:
        if self._score_path is not None:
            self.load_score(self._score_path)

    def export_score(self) -> None:
        if self.document is None:
            self.notify("Nothing to export", "Open and analyse a score first.")
            return
        accepted = sum(
            1
            for suggestion in self.suggestion_panel.suggestions
            if suggestion.status is SuggestionStatus.ACCEPTED
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export corrected score",
            "",
            "MusicXML (*.musicxml);;Compressed (*.mxl);;MuseScore (*.mscz)",
        )
        if not path:
            return
        try:
            written = self.document.save(path)
        except ProofreaderError as exc:
            self.notify("Could not export", str(exc))
            return
        self.statusBar().showMessage(f"{accepted} correction(s) written to {written}")

    def export_report(self) -> None:
        if self.score is None:
            self.notify("Nothing to export", "Open and analyse a score first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", "", "HTML (*.html);;JSON (*.json);;Text (*.txt)"
        )
        if not path:
            return
        from ..models import AnalysisReport

        report = AnalysisReport(
            score_summary=self.score.summary(),
            source_path=str(self._score_path or ""),
            suggestions=tuple(self.suggestion_panel.suggestions),
        )
        written = write_report(report, path)
        self.statusBar().showMessage(f"Report written to {written}")

    # -- review actions ------------------------------------------------------------

    def _on_selection(self, suggestion: Suggestion | None) -> None:
        self.notation_view.set_suggestion(suggestion)
        self.scan_view.set_region(suggestion.target.region if suggestion else None)

    def _accept_current(self) -> None:
        suggestion = self.suggestion_panel.current()
        if suggestion is not None and suggestion.status is SuggestionStatus.PENDING:
            self.accept_suggestion(suggestion)

    def _reject_current(self) -> None:
        suggestion = self.suggestion_panel.current()
        if suggestion is not None:
            self._set_status(suggestion, SuggestionStatus.REJECTED)

    def _ignore_current(self) -> None:
        suggestion = self.suggestion_panel.current()
        if suggestion is not None:
            self._set_status(suggestion, SuggestionStatus.IGNORED)

    def accept_suggestion(self, suggestion: Suggestion) -> None:
        """Apply a correction to the in-memory document and show the result."""
        if self.applier is None or self.document is None:
            return
        if self._needs_reanalysis:
            self.notify(
                "Re-analysis needed",
                "A previous correction added or removed an element, which moves everything after "
                "it. Press F5 to re-analyse before accepting more.",
            )
            return
        if not suggestion.is_actionable:
            return
        try:
            self.applier.apply_suggestion(suggestion)
        except ProofreaderError as exc:
            self.notify("Could not apply correction", str(exc))
            return

        self.score = parse_score(self.document)
        self.notation_view.set_score(self.score)
        self._set_status(suggestion, SuggestionStatus.ACCEPTED)
        if self.applier.has_structural_changes:
            self._needs_reanalysis = True
            self.statusBar().showMessage(
                "Correction applied. It changed the structure of the bar — press F5 to "
                "re-analyse before accepting more."
            )

    def _set_status(self, suggestion: Suggestion, status: SuggestionStatus) -> None:
        updated = suggestion.with_status(status)
        self.suggestion_panel.update_suggestion(updated)
        self.notation_view.set_suggestion(updated)
        self.suggestion_panel.select_next_pending()

    def undo(self) -> None:
        if self.applier is None or self.document is None:
            return
        if not self.applier.undo_last():
            self.statusBar().showMessage("Nothing to undo.")
            return
        self.score = parse_score(self.document)
        self.notation_view.set_score(self.score)
        self._needs_reanalysis = self.applier.has_structural_changes
        self.statusBar().showMessage("Correction undone.")

    # -- lifecycle -----------------------------------------------------------------

    def _ask_discard(self, accepted: int) -> bool:
        answer = QMessageBox.question(
            self,
            "Unsaved corrections",
            f"{accepted} accepted correction(s) have not been exported. Quit anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer is QMessageBox.StandardButton.Yes

    def _show_message(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def] # noqa: N802 - Qt override
        accepted = sum(
            1
            for suggestion in self.suggestion_panel.suggestions
            if suggestion.status is SuggestionStatus.ACCEPTED
        )
        if accepted and not self.confirm_discard(accepted):
            event.ignore()
            return
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        event.accept()
