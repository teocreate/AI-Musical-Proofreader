"""Score structure: attributes, measures, parts, and the score itself."""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .events import NO_REF, Barline, Direction, ElementRef, Event, Note, Rest
from .events import Chord as ChordEvent
from .geometry import SourceRegion
from .pitch import Pitch, Step
from .rational import Rational

__all__ = [
    "AttributeChange",
    "Clef",
    "ClefSign",
    "KeySignature",
    "Measure",
    "MeasureAttributes",
    "Mode",
    "ParseIssue",
    "Part",
    "PartGroup",
    "Score",
    "ScoreMetadata",
    "TimeSignature",
    "Transpose",
]

#: Order in which sharps and flats are added to a key signature.
SHARP_ORDER: tuple[Step, ...] = (Step.F, Step.C, Step.G, Step.D, Step.A, Step.E, Step.B)
FLAT_ORDER: tuple[Step, ...] = tuple(reversed(SHARP_ORDER))

_TONIC_BY_FIFTHS_MAJOR: dict[int, str] = {
    -7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F",
    0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
}  # fmt: skip


class Mode(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    DORIAN = "dorian"
    PHRYGIAN = "phrygian"
    LYDIAN = "lydian"
    MIXOLYDIAN = "mixolydian"
    AEOLIAN = "aeolian"
    IONIAN = "ionian"
    LOCRIAN = "locrian"
    NONE = "none"


class KeySignature(BaseModel):
    """A key signature expressed the way notation does it: a count of sharps or flats."""

    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    fifths: int = Field(default=0, ge=-7, le=7)
    mode: Mode = Mode.MAJOR
    staff: int | None = Field(default=None, description="None = applies to all staves")

    @property
    def altered_steps(self) -> dict[Step, int]:
        """Which letter names the signature alters, and by how much."""
        if self.fifths > 0:
            return dict.fromkeys(SHARP_ORDER[: self.fifths], 1)
        if self.fifths < 0:
            return dict.fromkeys(FLAT_ORDER[: -self.fifths], -1)
        return {}

    def alter_for(self, step: Step) -> int:
        """Alteration this signature imposes on ``step`` absent any accidental."""
        return self.altered_steps.get(step, 0)

    @property
    def tonic_name(self) -> str:
        """Tonic letter name, honouring ``mode`` for the relative minor."""
        major = _TONIC_BY_FIFTHS_MAJOR.get(self.fifths, "C")
        if self.mode is not Mode.MINOR:
            return major
        return _TONIC_BY_FIFTHS_MAJOR.get(self.fifths + 3, major)

    @property
    def tonic_pitch_class(self) -> int:
        base = Pitch.from_name(self.tonic_name + "4")
        return base.pitch_class

    def describe(self) -> str:
        if self.fifths == 0:
            return f"{self.tonic_name} {self.mode.value}"
        marks = f"{abs(self.fifths)}{'#' if self.fifths > 0 else 'b'}"
        return f"{self.tonic_name} {self.mode.value} ({marks})"


class TimeSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    beats: str = "4"
    beat_type: int = 4
    symbol: str | None = Field(default=None, description="'common', 'cut', 'single-number' …")
    senza_misura: bool = False

    @property
    def beat_count(self) -> Fraction:
        """Numerator, supporting additive signatures like ``3+2/8``."""
        total = Fraction(0)
        for part in self.beats.replace(" ", "").split("+"):
            if part:
                total += Fraction(part)
        return total

    @property
    def measure_length(self) -> Fraction:
        """Nominal bar length in quarter notes."""
        if self.senza_misura or self.beat_type == 0:
            return Fraction(0)
        return self.beat_count * Fraction(4, self.beat_type)

    @property
    def is_compound(self) -> bool:
        """6/8, 9/8, 12/8 … where the felt beat is a dotted note."""
        return self.beat_type >= 8 and self.beat_count % 3 == 0 and self.beat_count > 3

    @property
    def beat_unit(self) -> Fraction:
        """Length of one felt beat, used for rhythmic-plausibility scoring."""
        quarter = Fraction(4, self.beat_type) if self.beat_type else Fraction(1)
        return quarter * 3 if self.is_compound else quarter

    def describe(self) -> str:
        return "senza misura" if self.senza_misura else f"{self.beats}/{self.beat_type}"


class ClefSign(StrEnum):
    G = "G"
    F = "F"
    C = "C"
    PERCUSSION = "percussion"
    TAB = "TAB"
    NONE = "none"


#: Diatonic index of the pitch each clef names, when placed on its reference line.
_CLEF_REFERENCE: dict[ClefSign, int] = {
    ClefSign.G: Pitch.from_name("G4").diatonic,
    ClefSign.F: Pitch.from_name("F3").diatonic,
    ClefSign.C: Pitch.from_name("C4").diatonic,
}


class Clef(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    sign: ClefSign = ClefSign.G
    line: int = 2
    octave_change: int = 0
    staff: int = 1

    @property
    def bottom_line_diatonic(self) -> int:
        """Diatonic index of the pitch sitting on the *bottom* staff line.

        This is the anchor the renderer and every ledger-line heuristic needs: a note's vertical
        position is ``pitch.diatonic - bottom_line_diatonic`` half-spaces above the bottom line.
        """
        reference = _CLEF_REFERENCE.get(self.sign, _CLEF_REFERENCE[ClefSign.G])
        return reference - (self.line - 1) * 2 + self.octave_change * 7

    def staff_position(self, pitch: Pitch) -> int:
        """Half-space offset above the bottom line. 0 = bottom line, 8 = top line."""
        return pitch.diatonic - self.bottom_line_diatonic

    def ledger_lines(self, pitch: Pitch) -> int:
        """How many ledger lines the notehead needs. Sustained large values mean wrong clef."""
        position = self.staff_position(pitch)
        if position < 0:
            return (-position) // 2
        if position > 8:
            return (position - 8) // 2
        return 0

    def describe(self) -> str:
        suffix = ""
        if self.octave_change:
            suffix = f" {self.octave_change:+d} octave"
        return f"{self.sign.value}{self.line}{suffix}"


class Transpose(BaseModel):
    """Written-to-sounding transposition for transposing instruments.

    Harmonic analysis on a Bb clarinet part without applying this produces nonsense, so the
    analysis context always works in sounding pitch while suggestions are always phrased in
    written pitch — that is what the user sees on the page.
    """

    model_config = ConfigDict(frozen=True)

    diatonic: int = 0
    chromatic: int = 0
    octave_change: int = 0
    double: bool = False

    @property
    def is_identity(self) -> bool:
        return self.diatonic == 0 and self.chromatic == 0 and self.octave_change == 0

    def apply(self, pitch: Pitch) -> Pitch:
        """Written pitch → sounding pitch."""
        if self.is_identity:
            return pitch
        target_diatonic = pitch.diatonic + self.diatonic + self.octave_change * 7
        target_midi = pitch.midi + self.chromatic + self.octave_change * 12
        step = Step.from_index(target_diatonic % 7)
        octave = target_diatonic // 7
        natural_midi = (octave + 1) * 12 + step.semitones
        return Pitch(step=step, alter=target_midi - natural_midi, octave=octave)


class MeasureAttributes(BaseModel):
    """The attribute state in force at the start of a measure."""

    model_config = ConfigDict(frozen=True)

    divisions: int = Field(default=1, ge=1)
    key: KeySignature = KeySignature()
    keys_by_staff: dict[int, KeySignature] = Field(default_factory=dict)
    time: TimeSignature | None = None
    clefs: dict[int, Clef] = Field(default_factory=dict)
    staves: int = 1

    def key_for_staff(self, staff: int) -> KeySignature:
        return self.keys_by_staff.get(staff, self.key)

    def clef_for_staff(self, staff: int) -> Clef:
        return self.clefs.get(staff, Clef(staff=staff))


class AttributeChange(BaseModel):
    """A mid-measure ``<attributes>`` element (clef changes, most often)."""

    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    onset: Rational
    key: KeySignature | None = None
    time: TimeSignature | None = None
    clefs: dict[int, Clef] = Field(default_factory=dict)
    divisions: int | None = None


class Measure(BaseModel):
    """One measure of one part, across all its staves and voices."""

    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    index: int = Field(ge=0, description="0-based position in the part; the canonical identifier")
    number: str = Field(description="Printed number, which may be '1a' or repeat between parts")
    implicit: bool = Field(default=False, description="Pickup or continuation bar, unnumbered")
    attributes: MeasureAttributes = MeasureAttributes()
    attribute_changes: tuple[AttributeChange, ...] = ()
    events: tuple[Event, ...] = ()
    directions: tuple[Direction, ...] = ()
    barlines: tuple[Barline, ...] = ()
    region: SourceRegion | None = None
    width: float | None = Field(default=None, description="MusicXML layout width, if exported")

    # -- access helpers ------------------------------------------------------------

    @property
    def voices(self) -> tuple[str, ...]:
        """Voice ids present, in first-appearance order."""
        seen: dict[str, None] = {}
        for event in self.events:
            seen.setdefault(event.voice, None)
        return tuple(seen)

    def events_in_voice(self, voice: str) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.voice == voice)

    def events_on_staff(self, staff: int) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.staff == staff)

    def iter_notes(self) -> Iterator[Note]:
        """Every sounding note, chord members included, in event order."""
        for event in self.events:
            if isinstance(event, Note):
                yield event
            elif isinstance(event, ChordEvent):
                yield from event.notes

    def iter_rests(self) -> Iterator[Rest]:
        for event in self.events:
            if isinstance(event, Rest):
                yield event

    @property
    def nominal_length(self) -> Fraction:
        """Bar length implied by the time signature; ``0`` when unknown."""
        return self.attributes.time.measure_length if self.attributes.time else Fraction(0)

    def voice_length(self, voice: str) -> Fraction:
        """Sounding length actually filled by a voice."""
        end = Fraction(0)
        for event in self.events:
            if event.voice == voice and event.duration.is_measured:
                end = max(end, event.end)
        return end

    @property
    def is_empty(self) -> bool:
        return not self.events


class PartGroup(BaseModel):
    """A bracket/brace grouping in the score order (e.g. the string section)."""

    model_config = ConfigDict(frozen=True)

    number: str = "1"
    name: str | None = None
    symbol: str | None = None
    part_ids: tuple[str, ...] = ()


class Part(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ElementRef = NO_REF
    id: str
    name: str = ""
    abbreviation: str = ""
    midi_program: int | None = None
    midi_channel: int | None = None
    staves: int = 1
    transpose: Transpose = Transpose()
    measures: tuple[Measure, ...] = ()

    @property
    def display_name(self) -> str:
        return self.name or self.abbreviation or self.id

    @property
    def is_transposing(self) -> bool:
        return not self.transpose.is_identity

    def iter_notes(self) -> Iterator[tuple[Measure, Note]]:
        for measure in self.measures:
            for note in measure.iter_notes():
                yield measure, note

    def measure_by_index(self, index: int) -> Measure | None:
        for measure in self.measures:
            if measure.index == index:
                return measure
        return None


class ScoreMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = ""
    subtitle: str = ""
    composer: str = ""
    lyricist: str = ""
    copyright: str = ""
    software: str = Field(default="", description="Encoder that produced the file")
    encoding_date: str = ""
    source_path: str = ""
    source_format: str = Field(default="musicxml", description="musicxml | mxl | mscz")

    @property
    def looks_like_musescore(self) -> bool:
        return "musescore" in self.software.lower()


class ParseIssue(BaseModel):
    """Something wrong with the file itself, as opposed to with the music.

    Kept separate from suggestions: a user cannot "accept" a malformed measure, and mixing the
    two would pollute the precision metric.
    """

    model_config = ConfigDict(frozen=True)

    severity: Literal["info", "warning", "error"] = "warning"
    message: str
    part_id: str | None = None
    measure_index: int | None = None
    detail: str | None = None


class Score(BaseModel):
    """The complete internal representation of a parsed score."""

    model_config = ConfigDict(frozen=True)

    metadata: ScoreMetadata = ScoreMetadata()
    parts: tuple[Part, ...] = ()
    part_groups: tuple[PartGroup, ...] = ()
    issues: tuple[ParseIssue, ...] = ()

    @property
    def measure_count(self) -> int:
        return max((len(part.measures) for part in self.parts), default=0)

    @property
    def note_count(self) -> int:
        return sum(1 for _ in self.iter_notes())

    def part_by_id(self, part_id: str) -> Part | None:
        for part in self.parts:
            if part.id == part_id:
                return part
        return None

    def iter_notes(self) -> Iterator[tuple[Part, Measure, Note]]:
        for part in self.parts:
            for measure in part.measures:
                for note in measure.iter_notes():
                    yield part, measure, note

    def iter_measures(self) -> Iterator[tuple[Part, Measure]]:
        for part in self.parts:
            for measure in part.measures:
                yield part, measure

    def summary(self) -> str:
        title = self.metadata.title or "(untitled)"
        return (
            f"{title}: {len(self.parts)} part(s), {self.measure_count} measures, "
            f"{self.note_count} notes"
        )
