"""Fixture construction and error injection.

Ships inside the package rather than in ``tests/`` because the regression corpus generator and
the evaluation harness in ``scripts/`` need it too, and because users can use it to build their
own labelled corpora from clean scores they already trust.
"""

from .builder import (
    MeasureSpec,
    NoteSpec,
    PartSpec,
    ScoreSpec,
    build_musicxml,
    duration_to_type,
    parse_pattern,
    parse_pitch,
    write_musicxml,
)
from .corpus import CORPUS, build_corpus_score, corpus_names
from .corruption import (
    CorruptionResult,
    Corruptor,
    ErrorKind,
    InjectedError,
    corrupt_file,
)

__all__ = [
    "CORPUS",
    "CorruptionResult",
    "Corruptor",
    "ErrorKind",
    "InjectedError",
    "MeasureSpec",
    "NoteSpec",
    "PartSpec",
    "ScoreSpec",
    "build_corpus_score",
    "build_musicxml",
    "corpus_names",
    "corrupt_file",
    "duration_to_type",
    "parse_pattern",
    "parse_pitch",
    "write_musicxml",
]
