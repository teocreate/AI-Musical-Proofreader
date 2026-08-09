"""Performance budget.

The stated target is a 100-page score analysed in under 30 seconds. That number only means
something if something checks it, so this test builds a score of that size and asserts the budget.
Marked ``slow``; run with ``pytest -m slow``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_proofreader.config import AnalysisConfig, ProofreaderConfig
from ai_proofreader.pipeline import Proofreader
from ai_proofreader.testing import MeasureSpec, PartSpec, ScoreSpec, write_musicxml

#: Roughly a 100-page orchestral score: sixteen parts of four hundred bars.
PARTS = 16
MEASURES = 400
BUDGET_SECONDS = 30.0

PATTERNS = (
    "C4:1 D4:1 E4:1 F4:1",
    "G4:1 A4:1 B4:1 C5:1",
    "[C4,E4,G4]:2 R:2",
    "D4:1/2 E4:1/2 F4:1 G4:2",
)


def build_large_score(path: Path) -> Path:
    parts = [
        PartSpec(
            id=f"P{index + 1}",
            name=f"Part {index + 1}",
            measures=[
                MeasureSpec.single(PATTERNS[(measure + index) % len(PATTERNS)])
                for measure in range(MEASURES)
            ],
        )
        for index in range(PARTS)
    ]
    write_musicxml(ScoreSpec(title="Large score", parts=parts), str(path))
    return path


@pytest.mark.slow
def test_large_score_stays_within_budget(tmp_path: Path) -> None:
    path = build_large_score(tmp_path / "large.musicxml")

    started = time.perf_counter()
    result = Proofreader().analyze_file(path)
    elapsed = time.perf_counter() - started

    assert result.report.note_count >= 20000
    assert elapsed < BUDGET_SECONDS, (
        f"analysis took {elapsed:.1f}s for {result.report.note_count} notes, "
        f"budget is {BUDGET_SECONDS}s"
    )


@pytest.mark.slow
def test_sharding_beats_serial_analysis(tmp_path: Path) -> None:
    """Sharding by part is only worth its complexity if it actually wins."""
    path = build_large_score(tmp_path / "large.musicxml")
    serial_config = ProofreaderConfig(analysis=AnalysisConfig(parallel=False))

    started = time.perf_counter()
    Proofreader(serial_config).analyze_file(path)
    serial = time.perf_counter() - started

    started = time.perf_counter()
    Proofreader().analyze_file(path)
    parallel = time.perf_counter() - started

    assert parallel < serial * 1.1, f"parallel {parallel:.1f}s vs serial {serial:.1f}s"


@pytest.mark.slow
def test_sharded_and_serial_agree(tmp_path: Path) -> None:
    """Parallelism must not change the answer, only the time it takes to get it."""
    parts = [
        PartSpec(
            id=f"P{index + 1}",
            measures=[MeasureSpec.single(PATTERNS[measure % 4]) for measure in range(40)],
        )
        for index in range(3)
    ]
    path = tmp_path / "medium.musicxml"
    write_musicxml(ScoreSpec(parts=parts), str(path))

    sharded = ProofreaderConfig(analysis=AnalysisConfig(parallel_threshold_notes=0))
    serial = ProofreaderConfig(analysis=AnalysisConfig(parallel=False))

    left = {item.id for item in Proofreader(sharded).analyze_file(path).report.suggestions}
    right = {item.id for item in Proofreader(serial).analyze_file(path).report.suggestions}
    assert left == right
