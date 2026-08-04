"""Pitch representation.

Two distinct notions of "how high is this note" matter to a proofreader and conflating them is
the source of most bad OMR heuristics:

* **Chromatic** position (MIDI number) — what it sounds like.
* **Diatonic** position (staff line/space) — where the notehead physically sits.

An OMR engine that misreads a notehead's vertical position produces a *diatonic* error of ±1
(or rarely ±2) steps. An engine that misses an accidental produces a *chromatic* error of ±1
semitone with the diatonic position unchanged. Those two failure modes need different
detectors and different fixes, so both coordinates are first-class here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["ACCIDENTAL_ALTER", "ALTER_ACCIDENTAL", "Accidental", "Pitch", "Step"]

#: Semitone offset of each natural step above C.
_STEP_SEMITONES: dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_STEP_ORDER: tuple[str, ...] = ("C", "D", "E", "F", "G", "A", "B")


class Step(StrEnum):
    """The seven natural letter names."""

    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    A = "A"
    B = "B"

    @property
    def semitones(self) -> int:
        """Semitones above C in the same octave."""
        return _STEP_SEMITONES[self.value]

    @property
    def index(self) -> int:
        """Diatonic index within an octave, C=0 … B=6."""
        return _STEP_ORDER.index(self.value)

    @classmethod
    def from_index(cls, index: int) -> Step:
        """Step for a diatonic index, wrapping modulo 7."""
        return cls(_STEP_ORDER[index % 7])


class Accidental(StrEnum):
    """Printed accidental glyphs we model.

    This is the *engraved* symbol, which is not the same thing as :attr:`Pitch.alter`: a note can
    sound as F-sharp because of the key signature while carrying no accidental of its own.
    """

    DOUBLE_FLAT = "flat-flat"
    FLAT = "flat"
    NATURAL = "natural"
    SHARP = "sharp"
    DOUBLE_SHARP = "double-sharp"
    NATURAL_FLAT = "natural-flat"
    NATURAL_SHARP = "natural-sharp"
    OTHER = "other"

    @property
    def symbol(self) -> str:
        """Compact text form for UI and log lines."""
        return _ACCIDENTAL_SYMBOL.get(self, "?")


_ACCIDENTAL_SYMBOL: dict[Accidental, str] = {
    Accidental.DOUBLE_FLAT: "bb",
    Accidental.FLAT: "b",
    Accidental.NATURAL: "n",
    Accidental.SHARP: "#",
    Accidental.DOUBLE_SHARP: "x",
    Accidental.NATURAL_FLAT: "nb",
    Accidental.NATURAL_SHARP: "n#",
    Accidental.OTHER: "?",
}

#: Alteration in semitones implied by each printed accidental.
ACCIDENTAL_ALTER: dict[Accidental, int] = {
    Accidental.DOUBLE_FLAT: -2,
    Accidental.FLAT: -1,
    Accidental.NATURAL: 0,
    Accidental.SHARP: 1,
    Accidental.DOUBLE_SHARP: 2,
    Accidental.NATURAL_FLAT: -1,
    Accidental.NATURAL_SHARP: 1,
}

#: The canonical accidental for a given alteration.
ALTER_ACCIDENTAL: dict[int, Accidental] = {
    -2: Accidental.DOUBLE_FLAT,
    -1: Accidental.FLAT,
    0: Accidental.NATURAL,
    1: Accidental.SHARP,
    2: Accidental.DOUBLE_SHARP,
}


class Pitch(BaseModel):
    """A written pitch: letter name, alteration and octave.

    ``octave`` follows scientific pitch notation as MusicXML does — middle C is ``C4``.
    """

    model_config = ConfigDict(frozen=True)

    step: Step
    alter: int = Field(default=0, ge=-4, le=4, description="Sounding alteration in semitones")
    octave: int = Field(ge=-2, le=10)

    @field_validator("step", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    # -- coordinates ---------------------------------------------------------------

    @property
    def midi(self) -> int:
        """MIDI note number; C4 = 60."""
        return (self.octave + 1) * 12 + self.step.semitones + self.alter

    @property
    def pitch_class(self) -> int:
        """Sounding pitch class, 0–11."""
        return self.midi % 12

    @property
    def diatonic(self) -> int:
        """Absolute staff position, ignoring accidentals. C4 = 28. Adjacent values are adjacent
        line/space positions on any staff, which is exactly the quantity OMR gets wrong."""
        return self.octave * 7 + self.step.index

    @property
    def name(self) -> str:
        """Human-readable name such as ``F#4`` or ``Bb3``."""
        acc = ""
        if self.alter:
            acc = ("#" * self.alter) if self.alter > 0 else ("b" * -self.alter)
        return f"{self.step.value}{acc}{self.octave}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    # -- derivations ---------------------------------------------------------------

    @classmethod
    def from_diatonic(cls, diatonic: int, alter: int = 0) -> Self:
        """Build a pitch from an absolute staff position."""
        return cls(step=Step.from_index(diatonic % 7), alter=alter, octave=diatonic // 7)

    @classmethod
    def from_name(cls, name: str) -> Self:
        """Parse ``"F#4"``, ``"Bb3"``, ``"C-1"``, ``"Fx4"`` (double sharp)."""
        text = name.strip()
        if not text:
            raise ValueError("empty pitch name")
        step = Step(text[0].upper())
        index = 1
        alter = 0
        while index < len(text) and text[index] in "#bx♯♭":
            char = text[index]
            alter += {"#": 1, "♯": 1, "b": -1, "♭": -1, "x": 2}[char]
            index += 1
        octave_text = text[index:]
        if not octave_text:
            raise ValueError(f"pitch {name!r} has no octave")
        return cls(step=step, alter=alter, octave=int(octave_text))

    def with_alter(self, alter: int) -> Self:
        """Same staff position, different alteration — the *missing accidental* fix."""
        return self.model_copy(update={"alter": alter})

    def step_shifted(self, steps: int, alter: int | None = None) -> Self:
        """Move ``steps`` staff positions up or down — the *misread notehead* fix."""
        return type(self).from_diatonic(
            self.diatonic + steps, self.alter if alter is None else alter
        )

    def semitones_to(self, other: Pitch) -> int:
        """Signed chromatic distance."""
        return other.midi - self.midi

    def steps_to(self, other: Pitch) -> int:
        """Signed diatonic distance in staff positions."""
        return other.diatonic - self.diatonic

    def is_enharmonic(self, other: Pitch) -> bool:
        """Same sound, possibly different spelling."""
        return self.midi == other.midi
