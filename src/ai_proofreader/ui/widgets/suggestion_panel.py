"""Right panel: the suggestion list and its verdict buttons.

Modelled on a spell checker, which is the interaction the brief asks for and the right one: a
queue of findings, each with a proposed replacement, dispatched with a single key. The keyboard
path is the primary one — a proofreader working through 200 findings should never have to reach
for the mouse.

    A  accept        R  reject        I  ignore
    N  next          P  previous
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...models import Severity, Suggestion, SuggestionStatus

__all__ = ["SuggestionPanel"]

_STATUS_MARK = {
    SuggestionStatus.PENDING: "",
    SuggestionStatus.ACCEPTED: "✓ ",
    SuggestionStatus.REJECTED: "✗ ",
    SuggestionStatus.IGNORED: "– ",
}

_SEVERITY_COLOUR = {
    Severity.CRITICAL: "#e05252",
    Severity.HIGH: "#e08b3c",
    Severity.MEDIUM: "#c9a227",
    Severity.LOW: "#7f8c99",
}


class SuggestionPanel(QWidget):
    """List of suggestions, the detail of the selected one, and the verdict buttons."""

    selection_changed = Signal(object)  # Suggestion | None
    accepted = Signal(object)
    rejected = Signal(object)
    ignored = Signal(object)

    def __init__(self, review_threshold: float = 0.5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suggestions: list[Suggestion] = []
        self._review_threshold = review_threshold

        self._summary = QLabel("No score analysed yet.")
        self._summary.setWordWrap(True)

        self._show_low = QCheckBox("Show low-confidence suggestions")
        self._show_low.setChecked(False)
        self._show_low.toggled.connect(lambda _: self._rebuild())

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)
        self._list.setAlternatingRowColors(True)

        self._detail = QTextBrowser()
        self._detail.setOpenExternalLinks(False)
        self._detail.setMinimumHeight(160)

        self._accept = QPushButton("Accept (A)")
        self._reject = QPushButton("Reject (R)")
        self._ignore = QPushButton("Ignore (I)")
        self._accept.clicked.connect(lambda: self._verdict(SuggestionStatus.ACCEPTED))
        self._reject.clicked.connect(lambda: self._verdict(SuggestionStatus.REJECTED))
        self._ignore.clicked.connect(lambda: self._verdict(SuggestionStatus.IGNORED))

        buttons = QHBoxLayout()
        buttons.addWidget(self._accept)
        buttons.addWidget(self._reject)
        buttons.addWidget(self._ignore)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._summary)
        layout.addWidget(self._show_low)
        layout.addWidget(self._list, 3)
        layout.addWidget(self._detail, 2)
        layout.addLayout(buttons)
        self._update_buttons(None)

    # -- content -------------------------------------------------------------------

    def set_suggestions(self, suggestions: list[Suggestion], summary: str = "") -> None:
        self._suggestions = list(suggestions)
        if summary:
            self._summary.setText(summary)
        self._rebuild()

    def update_suggestion(self, suggestion: Suggestion) -> None:
        """Replace one entry in place, preserving the selection."""
        for index, existing in enumerate(self._suggestions):
            if existing.id == suggestion.id:
                self._suggestions[index] = suggestion
                break
        row = self._list.currentRow()
        self._rebuild()
        if 0 <= row < self._list.count():
            self._list.setCurrentRow(row)

    @property
    def suggestions(self) -> list[Suggestion]:
        return list(self._suggestions)

    def current(self) -> Suggestion | None:
        item = self._list.currentItem()
        if item is None:
            return None
        identifier = item.data(Qt.ItemDataRole.UserRole)
        return next((item for item in self._suggestions if item.id == identifier), None)

    def _visible(self) -> list[Suggestion]:
        if self._show_low.isChecked():
            return self._suggestions
        return [
            suggestion
            for suggestion in self._suggestions
            if suggestion.confidence.calibrated >= self._review_threshold
        ]

    def _rebuild(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for suggestion in self._visible():
            item = QListWidgetItem(self._label(suggestion))
            item.setData(Qt.ItemDataRole.UserRole, suggestion.id)
            item.setForeground(QColor(_SEVERITY_COLOUR.get(suggestion.severity, "#7f8c99")))
            if suggestion.status is not SuggestionStatus.PENDING:
                font = QFont(item.font())
                font.setStrikeOut(suggestion.status is not SuggestionStatus.ACCEPTED)
                font.setBold(suggestion.status is SuggestionStatus.ACCEPTED)
                item.setFont(font)
            self._list.addItem(item)
        self._list.blockSignals(False)

        hidden = len(self._suggestions) - len(self._visible())
        pending = sum(1 for item in self._suggestions if item.status is SuggestionStatus.PENDING)
        accepted = sum(1 for item in self._suggestions if item.status is SuggestionStatus.ACCEPTED)
        note = f", {hidden} below the review threshold" if hidden else ""
        self._summary.setText(
            f"{len(self._suggestions)} suggestion(s): {pending} pending, {accepted} accepted{note}"
        )
        if self._list.count() and self._list.currentRow() < 0:
            self._list.setCurrentRow(0)
        elif not self._list.count():
            self._detail.clear()
            self._update_buttons(None)

    @staticmethod
    def _label(suggestion: Suggestion) -> str:
        mark = _STATUS_MARK[suggestion.status]
        change = (
            f"  {suggestion.current_repr} → {suggestion.suggested_repr}"
            if suggestion.suggested_repr
            else ""
        )
        return (
            f"{mark}{suggestion.confidence.percent:3d}%  "
            f"m.{suggestion.target.measure_number or suggestion.target.measure_index + 1}  "
            f"{suggestion.target.part_name}  ·  {suggestion.title}{change}"
        )

    # -- selection and verdicts ----------------------------------------------------

    def _on_selection(self, current: QListWidgetItem | None, _previous=None) -> None:  # type: ignore[no-untyped-def]
        suggestion = self.current()
        self._show_detail(suggestion)
        self._update_buttons(suggestion)
        self.selection_changed.emit(suggestion)

    def _show_detail(self, suggestion: Suggestion | None) -> None:
        if suggestion is None:
            self._detail.clear()
            return
        evidence = "".join(
            f"<li><b>{item.channel.value}</b> {item.score:.2f} — {item.summary}</li>"
            for item in suggestion.evidence
        )
        action = (
            "".join(f"<li>{edit.describe()}</li>" for edit in suggestion.edits)
            if suggestion.edits
            else "<li><i>No automatic correction — review this one by hand.</i></li>"
        )
        self._detail.setHtml(
            f"<h3 style='margin:0'>{suggestion.title}</h3>"
            f"<p style='color:#8a9099;margin:2px 0'>{suggestion.target.locator()} · "
            f"{suggestion.severity.value} · {suggestion.confidence.percent}% confidence · "
            f"{suggestion.detector}</p>"
            f"<p>{suggestion.explanation.replace(chr(10), '<br>')}</p>"
            f"<p><b>Would change:</b></p><ul>{action}</ul>"
            f"<p><b>Evidence:</b></p><ul>{evidence}</ul>"
        )

    def _update_buttons(self, suggestion: Suggestion | None) -> None:
        pending = suggestion is not None and suggestion.status is SuggestionStatus.PENDING
        self._accept.setEnabled(bool(pending and suggestion and suggestion.is_actionable))
        self._reject.setEnabled(bool(pending))
        self._ignore.setEnabled(bool(pending))
        if suggestion is not None and not suggestion.is_actionable:
            self._accept.setToolTip("This finding has no automatic correction to apply.")
        else:
            self._accept.setToolTip("")

    def _verdict(self, status: SuggestionStatus) -> None:
        suggestion = self.current()
        if suggestion is None:
            return
        signal = {
            SuggestionStatus.ACCEPTED: self.accepted,
            SuggestionStatus.REJECTED: self.rejected,
            SuggestionStatus.IGNORED: self.ignored,
        }[status]
        signal.emit(suggestion)

    # -- navigation ----------------------------------------------------------------

    def select_next(self) -> None:
        if self._list.count():
            self._list.setCurrentRow(min(self._list.currentRow() + 1, self._list.count() - 1))

    def select_previous(self) -> None:
        if self._list.count():
            self._list.setCurrentRow(max(self._list.currentRow() - 1, 0))

    def select_next_pending(self) -> None:
        """Jump to the next thing that still needs a decision — the spell-checker flow."""
        start = self._list.currentRow() + 1
        visible = self._visible()
        for offset in range(len(visible)):
            index = (start + offset) % max(1, len(visible))
            if visible[index].status is SuggestionStatus.PENDING:
                self._list.setCurrentRow(index)
                return
