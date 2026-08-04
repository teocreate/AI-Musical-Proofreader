"""Accidental semantics: what the page says versus what the file says.

Notation has a rule an OMR engine can get wrong in a way that leaves the file *internally
inconsistent*, which makes it detectable with no scan at all:

    An accidental applies to its note for the rest of the measure, at that octave, and to notes
    tied across the barline from it.

MusicXML stores the *sounding* alteration in ``<alter>`` and the *printed* glyph in
``<accidental>``. If a G-sharp is engraved on beat 1 and a plain G appears on beat 3 of the same
measure, a correct file says ``alter=1`` for both. A file that says ``alter=0`` for the second one
either lost the carry-over rule (OMR bug) or the engraver really did print a natural that the OMR
missed. Either way something is wrong, and we can say so precisely.

This module implements the rule and nothing else, so the detectors that use it stay short.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import ACCIDENTAL_ALTER, ALTER_ACCIDENTAL, Accidental, KeySignature, Note, Pitch, Step

__all__ = [
    "AccidentalState",
    "accidental_for_change",
    "expected_alter",
    "requires_printed_accidental",
    "spell_pitch_class",
]


@dataclass
class AccidentalState:
    """Accidentals in force during one measure, per staff and octave.

    Reset at every barline, as notation requires. Ties across the barline are handled by the
    caller passing ``carried`` when constructing the next measure's state.
    """

    key: KeySignature
    #: (staff, step, octave) → sounding alteration set by an explicit accidental this measure.
    explicit: dict[tuple[int, Step, int], int] = field(default_factory=dict)

    def alter_for(self, staff: int, pitch: Pitch) -> int:
        """The alteration the page implies for this staff position right now."""
        key_alter = self.key.alter_for(pitch.step)
        return self.explicit.get((staff, pitch.step, pitch.octave), key_alter)

    def observe(self, staff: int, pitch: Pitch, accidental: Accidental | None) -> None:
        """Record a note as it is engraved, updating the in-force accidentals."""
        if accidental is None or accidental is Accidental.OTHER:
            return
        alter = ACCIDENTAL_ALTER.get(accidental)
        if alter is None:
            return
        self.explicit[(staff, pitch.step, pitch.octave)] = alter

    def carry(self, keys: dict[tuple[int, Step, int], int]) -> None:
        """Seed the state with alterations tied over from the previous measure."""
        self.explicit.update(keys)

    def copy_with_key(self, key: KeySignature) -> AccidentalState:
        return AccidentalState(key=key, explicit=dict(self.explicit))


def expected_alter(state: AccidentalState, note: Note) -> int:
    """What ``note``'s ``alter`` should be, given the key and this measure's accidentals so far.

    Call this *before* ``state.observe`` for the same note.
    """
    if note.accidental is not None and note.accidental is not Accidental.OTHER:
        alter = ACCIDENTAL_ALTER.get(note.accidental)
        if alter is not None:
            return alter
    return state.alter_for(note.staff, note.pitch)


def accidental_for_change(current_alter: int, target_alter: int) -> Accidental | None:
    """The glyph needed to move a note from ``current_alter`` to ``target_alter``.

    Returns ``None`` when no accidental should be printed because the target already matches
    what is in force.
    """
    if current_alter == target_alter:
        return None
    return ALTER_ACCIDENTAL.get(target_alter, Accidental.OTHER)


def requires_printed_accidental(state: AccidentalState, staff: int, pitch: Pitch) -> bool:
    """Whether engraving ``pitch`` here needs an accidental glyph."""
    return state.alter_for(staff, pitch) != pitch.alter


def spell_pitch_class(
    pitch_class: int, key: KeySignature, prefer_sharp: bool | None = None
) -> Pitch:
    """Choose a reasonable spelling for a pitch class in a key, in octave 4.

    Used when a suggestion has to *name* a pitch that the current score does not contain — a
    proposed accidental insertion, for example. Preference follows the key signature: flat keys
    spell flats.
    """
    if prefer_sharp is None:
        prefer_sharp = key.fifths >= 0
    naturals = {0: Step.C, 2: Step.D, 4: Step.E, 5: Step.F, 7: Step.G, 9: Step.A, 11: Step.B}
    target = pitch_class % 12
    if target in naturals:
        return Pitch(step=naturals[target], alter=0, octave=4)
    if prefer_sharp:
        return Pitch(step=naturals[(target - 1) % 12], alter=1, octave=4)
    return Pitch(step=naturals[(target + 1) % 12], alter=-1, octave=4)
