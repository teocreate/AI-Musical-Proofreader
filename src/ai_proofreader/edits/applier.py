"""Applying corrections to the original document.

This is the only module allowed to change a user's score, and it does so as a **surgical patch**:
it locates the exact element the suggestion targets and edits it in place, leaving every other
byte of MuseScore's export alone. A one-note pitch fix produces a one-line diff.

Two properties matter more than anything else here:

* **Reversibility.** Every operation snapshots the element it is about to touch, so any edit can
  be undone exactly — including structural ones. The UI's undo and the "reject after accepting"
  flow both ride on this.
* **Schema order.** MusicXML's DTD fixes the order of a ``<note>``'s children. Appending an
  ``<accidental>`` at the end produces a file that MuseScore will open and quietly mangle, so
  insertions are placed at the schema-correct position rather than wherever is convenient.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

from lxml import etree

from ..models import (
    EditAction,
    EditOperation,
    InsertRestOp,
    RemoveElementOp,
    SetAccidentalOp,
    SetArticulationOp,
    SetClefOp,
    SetDurationOp,
    SetDynamicOp,
    SetKeyOp,
    SetPitchOp,
    SetSlurOp,
    SetTieOp,
    SetVoiceOp,
    SpannerRole,
    Suggestion,
)
from ..score_parser.document import SourceDocument, localname
from ..score_parser.errors import EditApplicationError

__all__ = ["NOTE_CHILD_ORDER", "AppliedEdit", "EditApplier"]

logger = logging.getLogger(__name__)

#: Schema order of ``<note>`` children (MusicXML 4.0). Used to place new elements correctly.
NOTE_CHILD_ORDER: tuple[str, ...] = (
    "grace",
    "cue",
    "chord",
    "pitch",
    "unpitched",
    "rest",
    "duration",
    "tie",
    "instrument",
    "footnote",
    "level",
    "voice",
    "type",
    "dot",
    "accidental",
    "time-modification",
    "stem",
    "notehead",
    "notehead-text",
    "staff",
    "beam",
    "notations",
    "lyric",
    "play",
    "listen",
)

_NOTATIONS_ORDER: tuple[str, ...] = (
    "footnote",
    "level",
    "tied",
    "slur",
    "tuplet",
    "glissando",
    "slide",
    "ornaments",
    "technical",
    "articulations",
    "dynamics",
    "fermata",
    "arpeggiate",
    "non-arpeggiate",
    "accidental-mark",
    "other-notation",
)

_CLEF_ORDER: tuple[str, ...] = ("sign", "line", "clef-octave-change")
_KEY_ORDER: tuple[str, ...] = ("cancel", "fifths", "mode")


def _insert_ordered(parent: etree._Element, child: etree._Element, order: tuple[str, ...]) -> None:
    """Insert ``child`` into ``parent`` at the position the schema requires."""
    name = localname(child.tag)
    try:
        rank = order.index(name)
    except ValueError:
        parent.append(child)
        return
    for index, existing in enumerate(parent.iterchildren()):
        existing_name = localname(existing.tag)
        existing_rank = order.index(existing_name) if existing_name in order else len(order)
        if existing_rank > rank:
            parent.insert(index, child)
            return
    parent.append(child)


def _find(parent: etree._Element, name: str) -> etree._Element | None:
    for child in parent.iterchildren():
        if localname(child.tag) == name:
            return child
    return None


def _find_all(parent: etree._Element, name: str) -> list[etree._Element]:
    return [child for child in parent.iterchildren() if localname(child.tag) == name]


def _set_text(
    parent: etree._Element, name: str, text: str, order: tuple[str, ...]
) -> etree._Element:
    element = _find(parent, name)
    if element is None:
        element = etree.Element(name)
        _insert_ordered(parent, element, order)
    element.text = text
    return element


def _remove(parent: etree._Element, name: str) -> None:
    for child in _find_all(parent, name):
        parent.remove(child)


@dataclass
class AppliedEdit:
    """One executed operation, with everything needed to undo it."""

    operation: EditOperation
    #: Deep copy of the target element before the change (``None`` for insertions).
    before: etree._Element | None
    #: Parent and index, needed to restore removals and undo insertions.
    parent: etree._Element | None = None
    index: int = -1
    inserted: etree._Element | None = None
    #: True when the edit changed the element *count*, which invalidates handles after it.
    structural: bool = False
    suggestion_id: str = ""


@dataclass
class EditApplier:
    """Applies edit operations to a :class:`SourceDocument`."""

    document: SourceDocument
    history: list[AppliedEdit] = field(default_factory=list)

    # -- public API ----------------------------------------------------------------

    def apply(self, operation: EditOperation, suggestion_id: str = "") -> AppliedEdit:
        """Execute one operation, recording it for undo."""
        element = self.document.try_element(operation.ref)
        if element is None:
            raise EditApplicationError(
                f"{operation.op}: element handle {operation.ref} is not in this document"
            )

        handler = getattr(self, f"_apply_{operation.op}", None)
        if handler is None:  # pragma: no cover - the union is closed
            raise EditApplicationError(f"no handler for operation {operation.op!r}")

        snapshot = copy.deepcopy(element)
        parent = element.getparent()
        index = list(parent).index(element) if parent is not None else -1

        record = handler(element, operation)
        record.operation = operation
        record.suggestion_id = suggestion_id
        if record.before is None and not record.structural:
            record.before = snapshot
            record.parent = parent
            record.index = index
        self.history.append(record)
        return record

    def apply_suggestion(self, suggestion: Suggestion) -> list[AppliedEdit]:
        """Execute every operation of one suggestion, rolling back if any of them fails."""
        applied: list[AppliedEdit] = []
        try:
            for operation in suggestion.edits:
                applied.append(self.apply(operation, suggestion_id=suggestion.id))
        except EditApplicationError:
            for _ in applied:
                self.undo_last()
            raise
        return applied

    def undo_last(self) -> bool:
        """Reverse the most recent edit. Returns ``False`` when there is nothing to undo."""
        if not self.history:
            return False
        record = self.history.pop()
        if record.inserted is not None:
            parent = record.inserted.getparent()
            if parent is not None:
                parent.remove(record.inserted)
            return True
        if record.before is not None and record.parent is not None:
            current = self.document.try_element(record.operation.ref)
            if current is not None and current.getparent() is record.parent:
                record.parent.replace(current, record.before)
            else:
                record.parent.insert(record.index, record.before)
            # The handle must now resolve to the restored element.
            self.document.rebind(record.operation.ref, record.before)
            return True
        return False

    def undo_all(self) -> int:
        count = 0
        while self.undo_last():
            count += 1
        return count

    @property
    def has_structural_changes(self) -> bool:
        """Whether any applied edit added or removed elements.

        Structural changes shift the positions of later elements, so the score must be re-parsed
        before any further suggestion is applied — the applier says so rather than letting a
        stale handle silently target the wrong note.
        """
        return any(record.structural for record in self.history)

    # -- operation handlers --------------------------------------------------------

    def _apply_set_pitch(self, element: etree._Element, operation: SetPitchOp) -> AppliedEdit:
        pitch = _find(element, "pitch")
        if pitch is None:
            raise EditApplicationError("set_pitch: target is not a pitched note")
        _set_text(pitch, "step", operation.step.value, ("step", "alter", "octave"))
        if operation.alter:
            _set_text(pitch, "alter", str(operation.alter), ("step", "alter", "octave"))
        else:
            _remove(pitch, "alter")
        _set_text(pitch, "octave", str(operation.octave), ("step", "alter", "octave"))

        if operation.set_accidental:
            self._write_accidental(element, operation.accidental, cautionary=False, editorial=False)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_accidental(
        self, element: etree._Element, operation: SetAccidentalOp
    ) -> AppliedEdit:
        self._write_accidental(
            element, operation.accidental, operation.cautionary, operation.editorial
        )
        return AppliedEdit(operation=operation, before=None)

    @staticmethod
    def _write_accidental(
        note: etree._Element, accidental: object, cautionary: bool, editorial: bool
    ) -> None:
        if accidental is None:
            _remove(note, "accidental")
            return
        element = _find(note, "accidental")
        if element is None:
            element = etree.Element("accidental")
            _insert_ordered(note, element, NOTE_CHILD_ORDER)
        element.text = accidental.value  # type: ignore[union-attr]
        if cautionary:
            element.set("cautionary", "yes")
        else:
            element.attrib.pop("cautionary", None)
        if editorial:
            element.set("editorial", "yes")
        else:
            element.attrib.pop("editorial", None)

    def _apply_set_tie(self, element: etree._Element, operation: SetTieOp) -> AppliedEdit:
        notations = _find(element, "notations")
        for wanted, role in ((operation.start, "start"), (operation.stop, "stop")):
            if wanted is None:
                continue
            existing = [
                child for child in _find_all(element, "tie") if (child.get("type") or "") == role
            ]
            if wanted and not existing:
                tie = etree.Element("tie")
                tie.set("type", role)
                _insert_ordered(element, tie, NOTE_CHILD_ORDER)
            elif not wanted:
                for child in existing:
                    element.remove(child)

            if wanted and notations is None:
                notations = etree.Element("notations")
                _insert_ordered(element, notations, NOTE_CHILD_ORDER)
            if notations is not None:
                tied = [
                    child
                    for child in _find_all(notations, "tied")
                    if (child.get("type") or "") == role
                ]
                if wanted and not tied:
                    marker = etree.Element("tied")
                    marker.set("type", role)
                    _insert_ordered(notations, marker, _NOTATIONS_ORDER)
                elif not wanted:
                    for child in tied:
                        notations.remove(child)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_slur(self, element: etree._Element, operation: SetSlurOp) -> AppliedEdit:
        notations = _find(element, "notations")
        role = (
            operation.role.value if isinstance(operation.role, SpannerRole) else str(operation.role)
        )
        if operation.action is EditAction.ADD:
            if notations is None:
                notations = etree.Element("notations")
                _insert_ordered(element, notations, NOTE_CHILD_ORDER)
            slur = etree.Element("slur")
            slur.set("type", role)
            slur.set("number", str(operation.number))
            if operation.placement:
                slur.set("placement", operation.placement)
            _insert_ordered(notations, slur, _NOTATIONS_ORDER)
        elif notations is not None:
            for child in _find_all(notations, "slur"):
                if (child.get("type") or "") == role and int(
                    child.get("number") or 1
                ) == operation.number:
                    notations.remove(child)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_duration(self, element: etree._Element, operation: SetDurationOp) -> AppliedEdit:
        _set_text(element, "duration", str(operation.ticks), NOTE_CHILD_ORDER)
        if operation.note_type:
            _set_text(element, "type", operation.note_type, NOTE_CHILD_ORDER)
        existing_dots = _find_all(element, "dot")
        for dot in existing_dots[operation.dots :]:
            element.remove(dot)
        for _ in range(operation.dots - len(existing_dots)):
            _insert_ordered(element, etree.Element("dot"), NOTE_CHILD_ORDER)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_voice(self, element: etree._Element, operation: SetVoiceOp) -> AppliedEdit:
        _set_text(element, "voice", operation.voice, NOTE_CHILD_ORDER)
        if operation.staff is not None:
            _set_text(element, "staff", str(operation.staff), NOTE_CHILD_ORDER)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_articulation(
        self, element: etree._Element, operation: SetArticulationOp
    ) -> AppliedEdit:
        notations = _find(element, "notations")
        if operation.action is EditAction.ADD:
            if notations is None:
                notations = etree.Element("notations")
                _insert_ordered(element, notations, NOTE_CHILD_ORDER)
            articulations = _find(notations, "articulations")
            if articulations is None:
                articulations = etree.Element("articulations")
                _insert_ordered(notations, articulations, _NOTATIONS_ORDER)
            marker = etree.SubElement(articulations, operation.name)
            if operation.placement:
                marker.set("placement", operation.placement)
        elif notations is not None:
            articulations = _find(notations, "articulations")
            if articulations is not None:
                for child in _find_all(articulations, operation.name):
                    articulations.remove(child)
                if len(articulations) == 0:
                    notations.remove(articulations)
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_dynamic(self, element: etree._Element, operation: SetDynamicOp) -> AppliedEdit:
        for direction_type in _find_all(element, "direction-type"):
            dynamics = _find(direction_type, "dynamics")
            if dynamics is None:
                continue
            for child in list(dynamics):
                dynamics.remove(child)
            etree.SubElement(dynamics, operation.value)
            return AppliedEdit(operation=operation, before=None)
        raise EditApplicationError("set_dynamic: no <dynamics> element under this direction")

    def _apply_set_clef(self, element: etree._Element, operation: SetClefOp) -> AppliedEdit:
        _set_text(element, "sign", operation.sign, _CLEF_ORDER)
        _set_text(element, "line", str(operation.line), _CLEF_ORDER)
        if operation.octave_change:
            _set_text(element, "clef-octave-change", str(operation.octave_change), _CLEF_ORDER)
        else:
            _remove(element, "clef-octave-change")
        return AppliedEdit(operation=operation, before=None)

    def _apply_set_key(self, element: etree._Element, operation: SetKeyOp) -> AppliedEdit:
        _set_text(element, "fifths", str(operation.fifths), _KEY_ORDER)
        if operation.mode:
            _set_text(element, "mode", operation.mode, _KEY_ORDER)
        return AppliedEdit(operation=operation, before=None)

    def _apply_insert_rest(self, element: etree._Element, operation: InsertRestOp) -> AppliedEdit:
        parent = element.getparent()
        if parent is None:
            raise EditApplicationError("insert_rest: target note has no parent measure")

        rest = etree.Element("note")
        etree.SubElement(rest, "rest")
        duration = etree.SubElement(rest, "duration")
        duration.text = str(operation.ticks)
        voice_element = _find(element, "voice")
        if voice_element is not None:
            voice = etree.SubElement(rest, "voice")
            voice.text = voice_element.text
        if operation.note_type:
            note_type = etree.SubElement(rest, "type")
            note_type.text = operation.note_type
        for _ in range(operation.dots):
            etree.SubElement(rest, "dot")
        staff_element = _find(element, "staff")
        if staff_element is not None:
            staff = etree.SubElement(rest, "staff")
            staff.text = staff_element.text

        index = list(parent).index(element)
        parent.insert(index if operation.before else index + 1, rest)
        return AppliedEdit(
            operation=operation,
            before=None,
            parent=parent,
            index=index,
            inserted=rest,
            structural=True,
        )

    def _apply_remove_element(
        self, element: etree._Element, operation: RemoveElementOp
    ) -> AppliedEdit:
        parent = element.getparent()
        if parent is None:
            raise EditApplicationError("remove_element: element has no parent")
        index = list(parent).index(element)
        snapshot = copy.deepcopy(element)
        parent.remove(element)
        return AppliedEdit(
            operation=operation,
            before=snapshot,
            parent=parent,
            index=index,
            structural=True,
        )
