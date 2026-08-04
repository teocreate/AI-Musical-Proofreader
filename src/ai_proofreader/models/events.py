"""Musical events: what happens inside a measure.

MusicXML models a chord as a run of ``<note>`` elements where every element after the first
carries a ``<chord/>`` flag, and it models simultaneous voices with ``<backup>``/``<forward>``
cursor jumps. Both are serialization conveniences that make analysis miserable. The IR
normalizes them: chords are a single :class:`Chord` event holding its members, and every event
carries an explicit ``onset`` measured from the start of its measure.

Round-tripping is preserved through ``ref`` handles — see
:mod:`ai_proofreader.score_parser.document`.
"""

from __future__ import annotations

from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .geometry import SourceRegion
from .pitch import Accidental, Pitch
from .rational import Rational

__all__ = [
    "NO_REF",
    "Barline",
    "BarlineLocation",
    "Chord",
    "Direction",
    "DirectionKind",
    "Duration",
    "ElementRef",
    "Event",
    "Lyric",
    "Note",
    "RepeatDirection",
    "Rest",
    "Spanner",
    "SpannerRole",
    "StemDirection",
    "TieState",
    "TimeModification",
]

#: Handle into :class:`~ai_proofreader.score_parser.document.SourceDocument`'s element table.
#: An ``int`` rather than an element pointer so the IR stays JSON-serializable and picklable
#: across process-pool workers.
ElementRef = int

#: Sentinel for IR objects that were synthesized rather than parsed.
NO_REF: ElementRef = -1


class TimeModification(BaseModel):
    """A tuplet ratio: ``actual`` notes in the time of ``normal``."""

    model_config = ConfigDict(frozen=True)

    actual_notes: int = Field(ge=1)
    normal_notes: int = Field(ge=1)
    normal_type: str | None = None

    @property
    def factor(self) -> Fraction:
        """Multiplier applied to the written note value."""
        return Fraction(self.normal_notes, self.actual_notes)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.actual_notes}:{self.normal_notes}"


class Duration(BaseModel):
    """How long an event lasts, in exact quarter notes, plus how it is written.

    ``quarter_length`` is authoritative for analysis; ``note_type``/``dots``/``time_modification``
    describe the engraving and are what an edit has to change to keep the file consistent.
    """

    model_config = ConfigDict(frozen=True)

    quarter_length: Rational
    note_type: str | None = Field(default=None, description="'quarter', 'eighth', 'breve', …")
    dots: int = Field(default=0, ge=0, le=4)
    time_modification: TimeModification | None = None
    ticks: int = Field(default=0, ge=0, description="Raw MusicXML <duration> in divisions")

    @property
    def is_measured(self) -> bool:
        """False for grace notes, which occupy no musical time."""
        return self.quarter_length > 0

    def describe(self) -> str:
        """Short label such as ``dotted quarter`` or ``eighth (3:2)``."""
        base = self.note_type or f"{self.quarter_length} QL"
        prefix = {0: "", 1: "dotted ", 2: "double-dotted ", 3: "triple-dotted "}.get(
            self.dots, f"{self.dots}-dotted "
        )
        suffix = f" ({self.time_modification})" if self.time_modification else ""
        return f"{prefix}{base}{suffix}"


class StemDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    NONE = "none"
    DOUBLE = "double"


class TieState(BaseModel):
    """Tie flags on a note.

    MusicXML says the same thing twice: ``<tie>`` under ``<note>`` is the *sounding* tie and
    ``<tied>`` under ``<notations>`` is the *printed* slur-shaped curve. OMR output frequently
    has one without the other, which is itself a useful signal, so both are kept.
    """

    model_config = ConfigDict(frozen=True)

    sounds_start: bool = False
    sounds_stop: bool = False
    prints_start: bool = False
    prints_stop: bool = False

    @property
    def starts(self) -> bool:
        return self.sounds_start or self.prints_start

    @property
    def stops(self) -> bool:
        return self.sounds_stop or self.prints_stop

    @property
    def is_inconsistent(self) -> bool:
        """A sounding tie with no printed curve (or vice versa) — usually an OMR artefact."""
        return (self.sounds_start != self.prints_start) or (self.sounds_stop != self.prints_stop)


class SpannerRole(StrEnum):
    START = "start"
    STOP = "stop"
    CONTINUE = "continue"


class Spanner(BaseModel):
    """One endpoint of a slur, wedge, octave shift or similar bracketed object."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(description="'slur', 'wedge', 'octave-shift', 'tuplet', 'glissando' …")
    role: SpannerRole
    number: int = Field(default=1, description="MusicXML spanner id, disambiguates nesting")
    placement: str | None = None
    value: str | None = Field(default=None, description="Type-specific payload, e.g. wedge type")


class Lyric(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int = 1
    text: str = ""
    syllabic: str | None = None


class _EventBase(BaseModel):
    """Fields shared by everything that occupies a slot in a voice."""

    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    onset: Rational = Field(description="Offset in quarter notes from the start of the measure")
    duration: Duration
    voice: str = "1"
    staff: int = 1
    region: SourceRegion | None = None

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration.quarter_length


class Note(BaseModel):
    """A single sounding pitch.

    A note that is part of a chord is *not* represented standalone — see :class:`Chord`. This
    class is used both for standalone notes and as a chord member, in which case ``onset``,
    ``duration``, ``voice`` and ``staff`` mirror the parent chord.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["note"] = "note"
    ref: ElementRef = NO_REF
    onset: Rational
    duration: Duration
    voice: str = "1"
    staff: int = 1
    region: SourceRegion | None = None

    pitch: Pitch
    accidental: Accidental | None = Field(
        default=None, description="Printed accidental, independent of Pitch.alter"
    )
    accidental_cautionary: bool = False
    accidental_editorial: bool = False
    is_grace: bool = False
    grace_slash: bool = False
    is_cue: bool = False
    tie: TieState = TieState()
    spanners: tuple[Spanner, ...] = ()
    articulations: tuple[str, ...] = ()
    ornaments: tuple[str, ...] = ()
    technical: tuple[str, ...] = ()
    fermata: bool = False
    stem: StemDirection | None = None
    beams: tuple[str, ...] = ()
    notehead: str | None = None
    lyrics: tuple[Lyric, ...] = ()

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration.quarter_length

    @property
    def has_printed_accidental(self) -> bool:
        return self.accidental is not None

    @property
    def slurs(self) -> tuple[Spanner, ...]:
        return tuple(s for s in self.spanners if s.kind == "slur")

    def describe(self) -> str:
        acc = f" ({self.accidental.symbol})" if self.accidental else ""
        return f"{self.pitch.name}{acc} {self.duration.describe()}"


class Rest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["rest"] = "rest"
    ref: ElementRef = NO_REF
    onset: Rational
    duration: Duration
    voice: str = "1"
    staff: int = 1
    region: SourceRegion | None = None

    is_measure_rest: bool = Field(
        default=False, description="MusicXML measure='yes' — fills whatever the bar length is"
    )
    display_step: str | None = None
    display_octave: int | None = None
    fermata: bool = False
    spanners: tuple[Spanner, ...] = ()

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration.quarter_length

    def describe(self) -> str:
        return f"{self.duration.describe()} rest"


class Chord(BaseModel):
    """Two or more notes sounding together in one voice."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["chord"] = "chord"
    ref: ElementRef = NO_REF
    onset: Rational
    duration: Duration
    voice: str = "1"
    staff: int = 1
    region: SourceRegion | None = None

    notes: tuple[Note, ...] = Field(min_length=1)

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration.quarter_length

    @property
    def pitches(self) -> tuple[Pitch, ...]:
        return tuple(note.pitch for note in self.notes)

    @property
    def lowest(self) -> Note:
        return min(self.notes, key=lambda n: n.pitch.midi)

    @property
    def highest(self) -> Note:
        return max(self.notes, key=lambda n: n.pitch.midi)

    def describe(self) -> str:
        return "[" + " ".join(n.pitch.name for n in self.notes) + f"] {self.duration.describe()}"


#: Anything that occupies a rhythmic slot in a voice.
Event = Annotated[Note | Rest | Chord, Field(discriminator="kind")]


class DirectionKind(StrEnum):
    DYNAMICS = "dynamics"
    WORDS = "words"
    METRONOME = "metronome"
    WEDGE = "wedge"
    OCTAVE_SHIFT = "octave-shift"
    PEDAL = "pedal"
    REHEARSAL = "rehearsal"
    SEGNO = "segno"
    CODA = "coda"
    DASHES = "dashes"
    BRACKET = "bracket"
    OTHER = "other"


class Direction(BaseModel):
    """A ``<direction>``: dynamics, tempo, hairpins, pedal, text.

    Directions attach to a point in time rather than to a note, which is why they are stored
    beside the event list rather than inside it.
    """

    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    kind: DirectionKind
    onset: Rational
    staff: int = 1
    voice: str | None = None
    value: str | None = Field(default=None, description="'mf', 'crescendo', 'Allegro' …")
    text: str | None = None
    placement: str | None = None
    tempo_bpm: float | None = None
    tempo_beat_unit: str | None = None
    spanner: Spanner | None = None
    region: SourceRegion | None = None

    def describe(self) -> str:
        return self.value or self.text or self.kind.value


class BarlineLocation(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class RepeatDirection(StrEnum):
    FORWARD = "forward"
    BACKWARD = "backward"


class Barline(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    location: BarlineLocation = BarlineLocation.RIGHT
    bar_style: str | None = None
    repeat: RepeatDirection | None = None
    repeat_times: int | None = None
    ending_numbers: tuple[int, ...] = ()
    ending_type: str | None = None
    fermata: bool = False
