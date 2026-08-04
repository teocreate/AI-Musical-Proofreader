"""End-to-end pipeline, CLI and corpus tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_proofreader.cli import main
from ai_proofreader.config import AnalysisConfig, ProofreaderConfig
from ai_proofreader.edits import EditApplier
from ai_proofreader.models import SuggestionKind
from ai_proofreader.pipeline import Proofreader, render_html, render_json, render_text
from ai_proofreader.score_parser import SourceDocument, parse_score
from ai_proofreader.testing import (
    Corruptor,
    ErrorKind,
    MeasureSpec,
    PartSpec,
    ScoreSpec,
    build_corpus_score,
    corpus_names,
)
from conftest import single_part


def motif_score(third_statement: str) -> ScoreSpec:
    motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
    filler = "A4:1 B4:1 C#5:1 D5:1"
    return ScoreSpec(
        title="Pipeline test",
        key_fifths=2,
        parts=[
            PartSpec(
                id="P1",
                name="Flute",
                measures=[
                    MeasureSpec.single(pattern)
                    for pattern in (
                        motif,
                        filler,
                        motif,
                        filler,
                        third_statement,
                        filler,
                        motif,
                        "D5:4",
                    )
                ],
            )
        ],
    )


class TestAnalysis:
    def test_clean_score_produces_nothing(self, score_file) -> None:  # type: ignore[no-untyped-def]
        motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
        result = Proofreader().analyze_file(score_file(motif_score(motif)))
        assert result.report.suggestions == ()

    def test_planted_error_is_found_and_explained(self, score_file) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        result = Proofreader().analyze_file(score_file(motif_score(broken)))
        assert len(result.report.suggestions) == 1

        found = result.report.suggestions[0]
        assert found.target.measure_index == 4
        assert found.current_repr == "F5"
        assert found.suggested_repr == "F#5"
        assert found.confidence.percent >= 70
        assert found.is_actionable
        # Several independent rules should have reached the same conclusion.
        assert len(found.evidence) >= 2

    def test_report_carries_timings_and_counts(self, score_file) -> None:  # type: ignore[no-untyped-def]
        result = Proofreader().analyze_file(score_file(motif_score("D5:4")))
        report = result.report
        assert report.note_count > 0
        assert report.measure_count == 8
        assert report.total_ms > 0
        assert set(report.detector_timings_ms) >= {"measure_duration", "motif_deviation"}

    def test_threshold_hides_weak_findings(self, score_file) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        strict = ProofreaderConfig(analysis=AnalysisConfig(min_confidence=0.99))
        assert Proofreader(strict).analyze_file(path).report.suggestions == ()

    def test_disabling_a_detector_silences_it(self, score_file) -> None:  # type: ignore[no-untyped-def]
        from ai_proofreader.config import DetectorConfig

        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        config = ProofreaderConfig(
            analysis=AnalysisConfig(
                detectors={
                    name: DetectorConfig(enabled=False)
                    for name in ("motif_deviation", "accidental_consistency", "chromatic_outlier")
                }
            )
        )
        assert Proofreader(config).analyze_file(path).report.suggestions == ()

    def test_a_broken_detector_does_not_lose_the_others(self, score_file, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """One bad rule must cost that rule, not the run."""
        from ai_proofreader.analysis.detectors import MotifDeviationDetector

        def explode(self, context):  # type: ignore[no-untyped-def]
            raise RuntimeError("detector bug")

        monkeypatch.setattr(MotifDeviationDetector, "run", explode)
        result = Proofreader().analyze_file(score_file(single_part("C4:1 D4:1 E4:1/2")))
        assert any(
            item.kind in {SuggestionKind.DURATION, SuggestionKind.DOT_ADD, SuggestionKind.REST}
            for item in result.report.suggestions
        )


class TestApplyingSuggestions:
    def test_accepting_a_suggestion_corrects_the_score(self, score_file) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        result = Proofreader().analyze_file(path)
        assert result.document is not None

        applier = EditApplier(result.document)
        applier.apply_suggestion(result.report.suggestions[0])

        corrected = parse_score(result.document)
        names = [note.pitch.name for note in corrected.parts[0].measures[4].iter_notes()]
        assert names == ["D5", "E5", "F#5", "E5", "D5"]

    def test_the_corrected_score_is_then_clean(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Applying every accepted suggestion must actually resolve them, not shuffle them."""
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        result = Proofreader().analyze_file(path)
        assert result.document is not None

        applier = EditApplier(result.document)
        for suggestion in result.report.suggestions:
            if suggestion.is_actionable:
                applier.apply_suggestion(suggestion)
        output = result.document.save(tmp_path / "corrected.musicxml")

        assert Proofreader().analyze_file(output).report.suggestions == ()


class TestReports:
    def test_text_report_mentions_every_suggestion(self, score_file) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        report = Proofreader().analyze_file(score_file(motif_score(broken))).report
        text = render_text(report, verbose=True)
        for suggestion in report.suggestions:
            assert suggestion.title in text
            assert suggestion.explanation.splitlines()[0] in text

    def test_json_round_trips(self, score_file) -> None:  # type: ignore[no-untyped-def]
        from ai_proofreader.models import AnalysisReport

        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        report = Proofreader().analyze_file(score_file(motif_score(broken))).report
        restored = AnalysisReport.model_validate(json.loads(render_json(report)))
        assert restored.suggestions[0].edits == report.suggestions[0].edits
        assert (
            restored.suggestions[0].confidence.percent == report.suggestions[0].confidence.percent
        )

    def test_html_is_self_contained(self, score_file) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        html = render_html(Proofreader().analyze_file(score_file(motif_score(broken))).report)
        assert html.startswith("<!doctype html>")
        assert "http://" not in html.replace("http://www.w3.org", "")
        assert "<script" not in html


class TestCommandLine:
    def test_analyze_writes_json(self, score_file, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        output = tmp_path / "report.json"
        assert main(["analyze", str(path), "-o", str(output)]) == 0
        data = json.loads(output.read_text())
        assert data["suggestions"][0]["suggested_repr"] == "F#5"

    def test_apply_writes_a_corrected_score(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        corrected = tmp_path / "corrected.musicxml"
        assert main(["apply", str(path), "-o", str(corrected), "--accept-above", "0.7"]) == 0
        score = parse_score(SourceDocument.load(corrected))
        assert [n.pitch.name for n in score.parts[0].measures[4].iter_notes()][2] == "F#5"

    def test_dry_run_writes_nothing(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        corrected = tmp_path / "corrected.musicxml"
        assert (
            main(["apply", str(path), "-o", str(corrected), "--accept-above", "0.7", "--dry-run"])
            == 0
        )
        assert not corrected.exists()

    def test_apply_defaults_to_changing_nothing(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Without an explicit acceptance, nothing is applied. Never silently modify the score."""
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        path = score_file(motif_score(broken))
        corrected = tmp_path / "corrected.musicxml"
        assert main(["apply", str(path), "-o", str(corrected)]) == 0
        assert not corrected.exists()

    def test_inspect_prints_structure(self, score_file, capsys) -> None:  # type: ignore[no-untyped-def]
        assert main(["inspect", str(score_file(motif_score("D5:4"))), "--measures", "1-2"]) == 0
        output = capsys.readouterr().out
        assert "Flute" in output
        assert "D major" in output

    def test_missing_file_is_a_clean_error(self, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        assert main(["analyze", str(tmp_path / "nope.musicxml")]) == 1
        assert "error:" in capsys.readouterr().err


class TestCorpus:
    @pytest.mark.parametrize("name", corpus_names())
    def test_clean_entries_are_almost_silent(self, name: str, score_file) -> None:  # type: ignore[no-untyped-def]
        """The precision contract, enforced. Clean music must produce essentially nothing; the
        allowance is one low-confidence finding per entry, and none at the review threshold."""
        result = Proofreader().analyze_file(
            score_file(build_corpus_score(name), f"{name}.musicxml")
        )
        assert len(result.report.suggestions) <= 1
        assert result.report.above(0.7) == ()

    def test_injected_errors_are_found(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        path = score_file(build_corpus_score("piano"), "piano.musicxml")
        document = SourceDocument.load(path)
        score = parse_score(document)
        corruptor = Corruptor(document, score, seed=7)
        result = corruptor.inject((ErrorKind.NOTEHEAD_SHIFT, ErrorKind.HALVED_DURATION), 4)
        corrupted = result.document.save(tmp_path / "corrupted.musicxml")
        assert result.count == 4

        report = Proofreader().analyze_file(corrupted).report
        flagged = {
            (suggestion.target.part_id, suggestion.target.measure_index)
            for suggestion in report.suggestions
        }
        hit = sum(1 for error in result.errors if (error.part_id, error.measure_index) in flagged)
        assert hit >= 2, f"only {hit}/4 injected errors reached the right measure"

    def test_corruption_is_reproducible(self, score_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        path = score_file(build_corpus_score("chorale"), "chorale.musicxml")

        def corrupt() -> list[str]:
            document = SourceDocument.load(path)
            score = parse_score(document)
            outcome = Corruptor(document, score, seed=3).inject((ErrorKind.NOTEHEAD_SHIFT,), 3)
            return [error.description + error.after for error in outcome.errors]

        assert corrupt() == corrupt()
