"""Notation-rendering and review-window tests.

The layout tests need no Qt at all — that separation is the point of the render package. The
window tests drive the real widgets offscreen, exercising the accept/reject flow end to end
rather than asserting that buttons exist.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import pytest

from ai_proofreader.models import Pitch, SuggestionStatus
from ai_proofreader.score_parser import SourceDocument, parse_score
from ai_proofreader.testing import MeasureSpec, PartSpec, ScoreSpec, write_musicxml
from ai_proofreader.ui.render import (
    DARK_THEME,
    Ellipse,
    LayoutEngine,
    Line,
    layout_score,
    render_svg,
)
from conftest import single_part

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def parse(spec: ScoreSpec, tmp_path: Path, name: str = "score.musicxml"):  # type: ignore[no-untyped-def]
    path = tmp_path / name
    write_musicxml(spec, str(path))
    document = SourceDocument.load(path)
    return parse_score(document), document, path


class TestLayout:
    def test_staff_lines_are_one_space_apart(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:4"), tmp_path)
        rendered = layout_score(score)
        lines = sorted(
            shape.y1
            for shape in rendered.shapes
            if isinstance(shape, Line) and shape.role == "staff"
        )
        assert len(lines) == 5
        gaps = [round(b - a, 6) for a, b in itertools.pairwise(lines)]
        assert gaps == [1.0, 1.0, 1.0, 1.0]

    def test_pitch_maps_to_the_right_staff_position(self, tmp_path: Path) -> None:
        """E4 sits on the bottom line of a treble staff, F4 in the space above it."""
        score, _, _ = parse(single_part("E4:2 F4:2"), tmp_path)
        rendered = layout_score(score)
        heads = [
            shape
            for shape in rendered.shapes
            if isinstance(shape, Ellipse) and shape.role == "notehead"
        ]
        bottom_line = max(
            shape.y1
            for shape in rendered.shapes
            if isinstance(shape, Line) and shape.role == "staff"
        )
        assert heads[0].cy == pytest.approx(bottom_line)
        assert heads[1].cy == pytest.approx(bottom_line - 0.5)

    def test_ledger_lines_appear_below_the_staff(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:4"), tmp_path)
        rendered = layout_score(score)
        ledgers = [shape for shape in rendered.shapes if shape.role == "ledger"]
        assert len(ledgers) == 1

    def test_stems_attach_to_the_notehead(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:1 D4:1 E4:1 F4:1"), tmp_path)
        rendered = layout_score(score)
        heads = {
            shape.ref: shape
            for shape in rendered.shapes
            if isinstance(shape, Ellipse) and shape.role == "notehead"
        }
        stems = {
            shape.ref: shape
            for shape in rendered.shapes
            if isinstance(shape, Line) and shape.role == "stem"
        }
        assert stems
        for ref, stem in stems.items():
            head = heads[ref]
            assert min(stem.y1, stem.y2) <= head.cy <= max(stem.y1, stem.y2)
            assert abs(stem.x1 - head.cx) < 1.0

    def test_stem_direction_follows_the_middle_line(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:2 C6:2"), tmp_path)
        rendered = layout_score(score)
        stems = [
            shape for shape in rendered.shapes if isinstance(shape, Line) and shape.role == "stem"
        ]
        low, high = stems[0], stems[1]
        assert low.y2 < low.y1, "a note below the middle line takes an up-stem"
        assert high.y2 > high.y1, "a note above the middle line takes a down-stem"

    def test_key_signature_accidentals_are_drawn(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("D4:4", key_fifths=3), tmp_path)
        rendered = layout_score(score)
        accidentals = [shape for shape in rendered.shapes if shape.role == "accidental"]
        assert accidentals, "three sharps should produce accidental shapes"

    def test_every_note_gets_a_highlightable_region(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:1 D4:1 E4:1 F4:1"), tmp_path)
        rendered = layout_score(score)
        for _, _, note in score.iter_notes():
            assert rendered.region_for(note.ref) is not None

    def test_chords_stack_on_one_stem(self, tmp_path: Path) -> None:
        # A half note, not a whole note: whole notes are correctly drawn without a stem.
        score, _, _ = parse(single_part("[C4,E4,G4]:2 R:2"), tmp_path)
        rendered = layout_score(score)
        heads = [
            shape
            for shape in rendered.shapes
            if isinstance(shape, Ellipse) and shape.role == "notehead"
        ]
        stems = [shape for shape in rendered.shapes if shape.role == "stem"]
        assert len(heads) == 3
        assert len(stems) == 1
        assert len({round(head.cx, 3) for head in heads}) == 1

    def test_long_scores_wrap_into_systems(self, tmp_path: Path) -> None:
        spec = ScoreSpec(
            parts=[PartSpec(id="P1", measures=[MeasureSpec.single("C4:1 D4:1 E4:1 F4:1")] * 12)]
        )
        score, _, _ = parse(spec, tmp_path)
        rendered = layout_score(score, page_width=70.0)
        assert len(rendered.systems) > 1
        assert rendered.systems[0].last_measure < rendered.systems[1].first_measure

    def test_measure_window_limits_what_is_drawn(self, tmp_path: Path) -> None:
        spec = ScoreSpec(parts=[PartSpec(id="P1", measures=[MeasureSpec.single("C4:4")] * 20)])
        score, _, _ = parse(spec, tmp_path)
        engine = LayoutEngine(score)
        rendered = engine.run(first_measure=8, last_measure=10)
        assert set(rendered.measure_spans) == {8, 9, 10}

    def test_empty_score_does_not_crash(self, tmp_path: Path) -> None:
        spec = ScoreSpec(parts=[PartSpec(id="P1", measures=[])])
        score, _, _ = parse(spec, tmp_path)
        rendered = layout_score(score)
        assert rendered.systems == []


class TestSvg:
    def test_output_is_a_standalone_document(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:1 D4:1 E4:1 F4:1"), tmp_path)
        svg = render_svg(layout_score(score))
        assert svg.startswith("<svg xmlns=")
        assert svg.rstrip().endswith("</svg>")
        assert "<image" not in svg  # nothing external to fetch

    def test_highlight_draws_a_box_and_recolours(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:1 D4:1 E4:1 F4:1"), tmp_path)
        rendered = layout_score(score)
        ref = next(score.iter_notes())[2].ref
        plain = render_svg(rendered)
        marked = render_svg(rendered, highlight_refs={ref})
        assert len(marked) > len(plain)
        assert "#d94f2b" in marked

    def test_dark_theme_changes_the_ink(self, tmp_path: Path) -> None:
        score, _, _ = parse(single_part("C4:4"), tmp_path)
        rendered = layout_score(score)
        assert DARK_THEME.paper in render_svg(rendered, theme=DARK_THEME)

    def test_text_is_escaped(self, tmp_path: Path) -> None:
        spec = ScoreSpec(
            parts=[
                PartSpec(id="P1", name="Horn <&> Trumpet", measures=[MeasureSpec.single("C4:4")])
            ]
        )
        score, _, _ = parse(spec, tmp_path)
        svg = render_svg(layout_score(score))
        assert "&lt;&amp;&gt;" in svg


@pytest.mark.ui
class TestReviewWindow:
    @pytest.fixture
    def app(self):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QApplication

        instance = QApplication.instance() or QApplication([])
        yield instance

    @pytest.fixture
    def window(self, app, tmp_path: Path):  # type: ignore[no-untyped-def]
        """A window with a score already analysed, bypassing the worker thread."""
        from ai_proofreader.pipeline import Proofreader
        from ai_proofreader.ui.main_window import MainWindow

        motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        filler = "A4:1 B4:1 C#5:1 D5:1"
        spec = ScoreSpec(
            key_fifths=2,
            parts=[
                PartSpec(
                    id="P1",
                    name="Flute",
                    measures=[
                        MeasureSpec.single(pattern)
                        for pattern in (motif, filler, motif, filler, broken, filler, motif, "D5:4")
                    ],
                )
            ],
        )
        path = tmp_path / "score.musicxml"
        write_musicxml(spec, str(path))

        window = MainWindow()
        window.confirm_discard = lambda _count: True
        messages: list[tuple[str, str]] = []
        window.notify = lambda title, text: messages.append((title, text))
        window.messages = messages  # type: ignore[attr-defined]
        result = Proofreader().analyze_file(path)
        window._score_path = path
        window._on_analysis_finished(result.score, result.document, result.report)
        yield window
        window.close()

    def test_score_and_suggestions_are_loaded(self, window) -> None:  # type: ignore[no-untyped-def]
        assert window.score is not None
        assert len(window.suggestion_panel.suggestions) == 1
        assert window.suggestion_panel.current() is not None

    def test_notation_panel_highlights_the_selection(self, window) -> None:  # type: ignore[no-untyped-def]
        suggestion = window.suggestion_panel.current()
        window.notation_view.set_suggestion(suggestion)
        svg = window.notation_view.current_svg()
        assert svg
        assert "#ff7a59" in svg  # the dark theme's highlight colour

    def test_accepting_corrects_the_in_memory_score(self, window) -> None:  # type: ignore[no-untyped-def]
        suggestion = window.suggestion_panel.current()
        assert suggestion is not None
        window.accept_suggestion(suggestion)

        measure = window.score.parts[0].measures[4]
        assert [note.pitch.name for note in measure.iter_notes()][2] == "F#5"
        updated = next(
            item for item in window.suggestion_panel.suggestions if item.id == suggestion.id
        )
        assert updated.status is SuggestionStatus.ACCEPTED

    def test_accepting_does_not_touch_the_file(self, window, tmp_path: Path) -> None:
        """Never silently modify the score: the disk copy stays as it was until an export."""
        original = (tmp_path / "score.musicxml").read_bytes()
        suggestion = window.suggestion_panel.current()
        window.accept_suggestion(suggestion)
        assert (tmp_path / "score.musicxml").read_bytes() == original

    def test_undo_reverts_an_accepted_correction(self, window) -> None:  # type: ignore[no-untyped-def]
        suggestion = window.suggestion_panel.current()
        window.accept_suggestion(suggestion)
        window.undo()
        measure = window.score.parts[0].measures[4]
        assert [note.pitch.name for note in measure.iter_notes()][2] == "F5"

    def test_rejecting_marks_without_changing_the_score(self, window) -> None:  # type: ignore[no-untyped-def]
        suggestion = window.suggestion_panel.current()
        window._set_status(suggestion, SuggestionStatus.REJECTED)
        updated = window.suggestion_panel.suggestions[0]
        assert updated.status is SuggestionStatus.REJECTED
        measure = window.score.parts[0].measures[4]
        assert [note.pitch.name for note in measure.iter_notes()][2] == "F5"

    def test_export_writes_the_corrected_file(self, window, tmp_path: Path) -> None:
        suggestion = window.suggestion_panel.current()
        window.accept_suggestion(suggestion)
        output = tmp_path / "corrected.musicxml"
        window.document.save(output)
        exported = parse_score(SourceDocument.load(output))
        assert [n.pitch.name for n in exported.parts[0].measures[4].iter_notes()][2] == "F#5"

    def test_low_confidence_suggestions_are_hidden_by_default(self, window) -> None:  # type: ignore[no-untyped-def]
        panel = window.suggestion_panel
        panel._review_threshold = 0.99
        panel._rebuild()
        assert panel._list.count() == 0
        panel._show_low.setChecked(True)
        assert panel._list.count() == 1


def test_render_package_imports_without_qt() -> None:
    """The layout engine must stay usable in a headless install with no PySide6."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['PySide6'] = None; "
            "from ai_proofreader.ui.render import layout_score, render_svg; print('ok')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_pitch_import_is_available() -> None:
    """Guards the lazy-import shim in ui/__init__: importing render must not drag in Qt."""
    assert Pitch.from_name("C4").midi == 60
