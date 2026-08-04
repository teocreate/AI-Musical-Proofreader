"""Reading scores: containers, XML, and the handle table that makes corrections surgical."""

from .document import MUSICXML_SUFFIXES, SourceDocument, find_musescore, localname
from .errors import (
    EditApplicationError,
    MuseScoreNotAvailableError,
    ProofreaderError,
    ScoreLoadError,
    ScoreParseError,
    UnsupportedFormatError,
)
from .musicxml_parser import MusicXmlParser, parse_score

__all__ = [
    "MUSICXML_SUFFIXES",
    "EditApplicationError",
    "MuseScoreNotAvailableError",
    "MusicXmlParser",
    "ProofreaderError",
    "ScoreLoadError",
    "ScoreParseError",
    "SourceDocument",
    "UnsupportedFormatError",
    "find_musescore",
    "localname",
    "parse_score",
]


def load_score(path: str, musescore_binary: str | None = None):  # type: ignore[no-untyped-def]
    """Load and parse in one step, returning ``(score, document)``.

    The document is returned alongside the score because corrections need it — the pair travels
    together for the whole lifetime of an analysis session.
    """
    document = SourceDocument.load(path, musescore_binary=musescore_binary)
    return parse_score(document), document
