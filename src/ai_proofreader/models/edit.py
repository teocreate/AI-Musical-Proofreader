"""Edit operations: the only vocabulary through which a score may be changed.

Detectors propose; they never mutate. Every proposed change is a serializable operation naming
the element it touches by :data:`~ai_proofreader.models.events.ElementRef` handle, so the same
suggestion can be reviewed in the UI, saved to JSON, replayed on the command line, and audited
afterwards. :mod:`ai_proofreader.edits.applier` is the only code allowed to execute them.

Operations are deliberately *small*. "Fix this measure" is not an operation; "set the duration of
note #418 to a dotted quarter" is. Small operations are individually reviewable and individually
reversible, which is what the never-silently-modify rule actually requires in practice.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .events import ElementRef, SpannerRole
from .pitch import Accidental, Step
from .rational import Rational

__all__ = [
    "EditAction",
    "EditOperation",
    "InsertRestOp",
    "RemoveElementOp",
    "SetAccidentalOp",
    "SetArticulationOp",
    "SetClefOp",
    "SetDurationOp",
    "SetDynamicOp",
    "SetKeyOp",
    "SetPitchOp",
    "SetSlurOp",
    "SetTieOp",
    "SetVoiceOp",
]


class EditAction(StrEnum):
    ADD = "add"
    REMOVE = "remove"


class _OpBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ElementRef = Field(description="Handle of the element this operation targets")


class SetPitchOp(_OpBase):
    """Replace a note's pitch. Covers both misread noteheads and missing accidentals."""

    op: Literal["set_pitch"] = "set_pitch"
    step: Step
    alter: int = Field(default=0, ge=-4, le=4)
    octave: int
    accidental: Accidental | None = Field(
        default=None, description="Printed accidental to write; None removes it"
    )
    set_accidental: bool = Field(
        default=True,
        description="When False the printed accidental is left exactly as it is, which is what "
        "you want when only the sounding alter was wrong",
    )

    def describe(self) -> str:
        acc = ("#" * self.alter) if self.alter > 0 else ("b" * -self.alter)
        return f"set pitch to {self.step.value}{acc}{self.octave}"


class SetAccidentalOp(_OpBase):
    """Add, change or remove a *printed* accidental without touching the sounding pitch."""

    op: Literal["set_accidental"] = "set_accidental"
    accidental: Accidental | None
    cautionary: bool = False
    editorial: bool = False

    def describe(self) -> str:
        if self.accidental is None:
            return "remove printed accidental"
        kind = "cautionary " if self.cautionary else ""
        return f"print {kind}{self.accidental.value}"


class SetTieOp(_OpBase):
    """Set or clear tie endpoints on a note. ``None`` leaves that endpoint untouched."""

    op: Literal["set_tie"] = "set_tie"
    start: bool | None = None
    stop: bool | None = None

    def describe(self) -> str:
        parts = []
        if self.start is not None:
            parts.append(("add" if self.start else "remove") + " tie start")
        if self.stop is not None:
            parts.append(("add" if self.stop else "remove") + " tie stop")
        return ", ".join(parts) or "no tie change"


class SetSlurOp(_OpBase):
    op: Literal["set_slur"] = "set_slur"
    action: EditAction
    role: SpannerRole = SpannerRole.START
    number: int = 1
    placement: str | None = None

    def describe(self) -> str:
        return f"{self.action.value} slur {self.role.value} (#{self.number})"


class SetDurationOp(_OpBase):
    """Change how long a note or rest lasts.

    ``ticks`` is in the measure's divisions and must be supplied by the proposer, which already
    knows the divisions; the applier refuses to guess.
    """

    op: Literal["set_duration"] = "set_duration"
    note_type: str | None = None
    dots: int = Field(default=0, ge=0, le=4)
    ticks: int = Field(ge=0)
    quarter_length: Rational = Field(description="Expected result, used to validate the patch")

    def describe(self) -> str:
        prefix = {0: "", 1: "dotted ", 2: "double-dotted "}.get(self.dots, f"{self.dots}-dotted ")
        return f"set duration to {prefix}{self.note_type or self.quarter_length}"


class SetVoiceOp(_OpBase):
    op: Literal["set_voice"] = "set_voice"
    voice: str
    staff: int | None = None

    def describe(self) -> str:
        staff = f", staff {self.staff}" if self.staff is not None else ""
        return f"move to voice {self.voice}{staff}"


class SetArticulationOp(_OpBase):
    op: Literal["set_articulation"] = "set_articulation"
    action: EditAction
    name: str = Field(description="MusicXML element name, e.g. 'staccato', 'accent'")
    placement: str | None = None

    def describe(self) -> str:
        return f"{self.action.value} {self.name}"


class SetDynamicOp(_OpBase):
    """Change a dynamic marking. The ref points at the ``<direction>`` element."""

    op: Literal["set_dynamic"] = "set_dynamic"
    value: str = Field(description="'p', 'mf', 'sfz' …")

    def describe(self) -> str:
        return f"set dynamic to {self.value}"


class SetClefOp(_OpBase):
    """Change a clef. The ref points at the ``<clef>`` element."""

    op: Literal["set_clef"] = "set_clef"
    sign: str
    line: int
    octave_change: int = 0

    def describe(self) -> str:
        return f"set clef to {self.sign}{self.line}"


class SetKeyOp(_OpBase):
    """Change a key signature. The ref points at the ``<key>`` element."""

    op: Literal["set_key"] = "set_key"
    fifths: int = Field(ge=-7, le=7)
    mode: str | None = None

    def describe(self) -> str:
        return f"set key signature to {self.fifths:+d} fifths"


class InsertRestOp(_OpBase):
    """Insert a rest immediately after the referenced element.

    Used for gaps left by an OMR engine that dropped a rest glyph. The applier copies the
    referenced element's ``<voice>``/``<staff>`` so the new rest lands in the right stream.
    """

    op: Literal["insert_rest"] = "insert_rest"
    note_type: str | None = None
    dots: int = Field(default=0, ge=0, le=4)
    ticks: int = Field(gt=0)
    quarter_length: Rational
    before: bool = Field(default=False, description="Insert before the ref instead of after")

    def describe(self) -> str:
        where = "before" if self.before else "after"
        return f"insert {self.note_type or self.quarter_length} rest {where}"


class RemoveElementOp(_OpBase):
    """Delete a note or rest element outright.

    The most destructive operation we have, so it is deliberately the only one that carries a
    mandatory justification string; the UI surfaces it verbatim before the user accepts.
    """

    op: Literal["remove_element"] = "remove_element"
    reason: str = Field(min_length=1)

    def describe(self) -> str:
        return f"remove element ({self.reason})"


EditOperation = Annotated[
    SetPitchOp
    | SetAccidentalOp
    | SetTieOp
    | SetSlurOp
    | SetDurationOp
    | SetVoiceOp
    | SetArticulationOp
    | SetDynamicOp
    | SetClefOp
    | SetKeyOp
    | InsertRestOp
    | RemoveElementOp,
    Field(discriminator="op"),
]
