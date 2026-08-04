"""Shared fixtures.

Every test builds its own score from the pattern language rather than loading a checked-in file,
so a test reads as the music it is about. The few tests that need a real file on disk get one
from :func:`score_file`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ai_proofreader.analysis import AnalysisContext
from ai_proofreader.config import AnalysisConfig
from ai_proofreader.models import Score
from ai_proofreader.score_parser import SourceDocument, parse_score
from ai_proofreader.testing import MeasureSpec, PartSpec, ScoreSpec, write_musicxml


@pytest.fixture
def score_file(tmp_path: Path) -> Callable[[ScoreSpec], Path]:
    """Write a ScoreSpec to disk and return the path."""

    def build(spec: ScoreSpec, name: str = "score.musicxml") -> Path:
        path = tmp_path / name
        write_musicxml(spec, str(path))
        return path

    return build


@pytest.fixture
def parsed(
    score_file: Callable[[ScoreSpec], Path],
) -> Callable[[ScoreSpec], tuple[Score, SourceDocument]]:
    """Build, write and parse a ScoreSpec, returning (score, document)."""

    def build(spec: ScoreSpec) -> tuple[Score, SourceDocument]:
        document = SourceDocument.load(score_file(spec))
        return parse_score(document), document

    return build


@pytest.fixture
def context(parsed) -> Callable[[ScoreSpec], AnalysisContext]:  # type: ignore[no-untyped-def]
    """Build an AnalysisContext straight from a ScoreSpec."""

    def build(spec: ScoreSpec, config: AnalysisConfig | None = None) -> AnalysisContext:
        score, _ = parsed(spec)
        return AnalysisContext(score, config or AnalysisConfig())

    return build


def single_part(*patterns: str, key_fifths: int = 0, time: tuple[int, int] = (4, 4)) -> ScoreSpec:
    """One part, one voice, one measure per pattern — the shape most rule tests want."""
    return ScoreSpec(
        key_fifths=key_fifths,
        time=time,
        parts=[
            PartSpec(
                id="P1",
                name="Test",
                measures=[MeasureSpec.single(pattern) for pattern in patterns],
            )
        ],
    )
