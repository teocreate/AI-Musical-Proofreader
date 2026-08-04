"""Exception hierarchy.

Every failure the user can plausibly cause — wrong file, missing MuseScore, corrupt zip — has a
distinct type with a message written for a musician rather than for a stack trace.
"""

from __future__ import annotations

__all__ = [
    "EditApplicationError",
    "MuseScoreNotAvailableError",
    "ProofreaderError",
    "ScoreLoadError",
    "ScoreParseError",
    "UnsupportedFormatError",
]


class ProofreaderError(Exception):
    """Base class for everything this application raises deliberately."""


class ScoreLoadError(ProofreaderError):
    """The file could not be read or is not a score we understand."""


class UnsupportedFormatError(ScoreLoadError):
    """The extension is not one we handle."""


class MuseScoreNotAvailableError(ScoreLoadError):
    """A ``.mscz`` was supplied but no MuseScore executable could be found.

    We do not reimplement MuseScore's private format (see ``docs/ARCHITECTURE.md`` §2.7), so
    conversion needs the real thing on PATH or in ``MUSESCORE_PATH``.
    """


class ScoreParseError(ProofreaderError):
    """The document is structurally broken beyond partial recovery."""


class EditApplicationError(ProofreaderError):
    """An edit could not be applied, or produced an invalid document."""
