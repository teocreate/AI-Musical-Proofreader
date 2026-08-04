"""Edit-application tests.

The product's core promise is that it never breaks a score. These tests are that promise written
down: patches are surgical, reversible, schema-correct, and everything outside the changed
element is byte-identical afterwards.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from lxml import etree

from ai_proofreader.edits import NOTE_CHILD_ORDER, EditApplier
from ai_proofreader.models import (
    Accidental,
    EditAction,
    RemoveElementOp,
    SetAccidentalOp,
    SetClefOp,
    SetDurationOp,
    SetKeyOp,
    SetPitchOp,
    SetSlurOp,
    SetTieOp,
    SetVoiceOp,
    SpannerRole,
    Step,
)
from ai_proofreader.score_parser import EditApplicationError, SourceDocument, parse_score
from ai_proofreader.testing import write_musicxml
from conftest import single_part


@pytest.fixture
def loaded(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A small score on disk, parsed, with its document."""

    def build(spec=None):  # type: ignore[no-untyped-def]
        path = tmp_path / "score.musicxml"
        write_musicxml(spec or single_part("C4:1 D4:1 E4:1 F4:1", "G4:4"), str(path))
        document = SourceDocument.load(path)
        return parse_score(document), document

    return build


def first_note(score):  # type: ignore[no-untyped-def]
    return next(score.parts[0].measures[0].iter_notes())


class TestPitchEdits:
    def test_set_pitch_changes_only_that_note(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        note = first_note(score)
        before = document.to_bytes().decode()

        EditApplier(document).apply(
            SetPitchOp(ref=note.ref, step=Step.D, alter=0, octave=4, set_accidental=False)
        )
        after = document.to_bytes().decode()

        reparsed = parse_score(document)
        names = [n.pitch.name for n in reparsed.parts[0].measures[0].iter_notes()]
        assert names == ["D4", "D4", "E4", "F4"]
        # A one-note change must be a small diff, not a re-engraving.
        assert sum(1 for a, b in zip(before.split("<note>"), after.split("<note>"), strict=False) if a != b) <= 2

    def test_adding_an_accidental_lands_in_schema_order(self, loaded) -> None:  # type: ignore[no-untyped-def]
        """Appending <accidental> after <notations> produces a file MuseScore silently mangles."""
        score, document = loaded()
        note = first_note(score)
        EditApplier(document).apply(
            SetPitchOp(
                ref=note.ref,
                step=Step.C,
                alter=1,
                octave=4,
                accidental=Accidental.SHARP,
                set_accidental=True,
            )
        )
        element = document.element(note.ref)
        names = [child.tag for child in element.iterchildren() if child.tag in NOTE_CHILD_ORDER]
        ranks = [NOTE_CHILD_ORDER.index(name) for name in names]
        assert ranks == sorted(ranks)
        assert parse_score(document).parts[0].measures[0].events[0].pitch.name == "C#4"  # type: ignore[union-attr]

    def test_removing_an_alter_removes_the_element(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("F#4:4", key_fifths=0))
        note = first_note(score)
        EditApplier(document).apply(
            SetPitchOp(ref=note.ref, step=Step.F, alter=0, octave=4, set_accidental=False)
        )
        element = document.element(note.ref)
        pitch = element.find("pitch")
        assert pitch.find("alter") is None
        assert parse_score(document).parts[0].measures[0].events[0].pitch.name == "F4"  # type: ignore[union-attr]

    def test_set_accidental_alone_leaves_the_sound_untouched(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("F#4:4", key_fifths=2))
        note = first_note(score)
        EditApplier(document).apply(
            SetAccidentalOp(ref=note.ref, accidental=Accidental.SHARP, cautionary=True)
        )
        reparsed = parse_score(document)
        changed = next(reparsed.parts[0].measures[0].iter_notes())
        assert changed.pitch.name == "F#4"
        assert changed.accidental_cautionary


class TestRhythmEdits:
    def test_set_duration_updates_type_and_dots(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        note = first_note(score)
        EditApplier(document).apply(
            SetDurationOp(
                ref=note.ref,
                note_type="quarter",
                dots=1,
                ticks=36,
                quarter_length=Fraction(3, 2),
            )
        )
        reparsed = parse_score(document)
        changed = next(reparsed.parts[0].measures[0].iter_notes())
        assert changed.duration.quarter_length == Fraction(3, 2)
        assert changed.duration.dots == 1

    def test_removing_dots_removes_the_elements(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("C4:3 D4:1"))
        note = first_note(score)
        assert note.duration.dots == 1
        EditApplier(document).apply(
            SetDurationOp(
                ref=note.ref, note_type="half", dots=0, ticks=48, quarter_length=Fraction(2)
            )
        )
        assert document.element(note.ref).find("dot") is None


class TestSpannerEdits:
    def test_tie_is_written_in_both_places(self, loaded) -> None:  # type: ignore[no-untyped-def]
        """MusicXML records a tie twice — sounding and printed — and a file with only one is the
        kind of half-tie that confuses every downstream tool."""
        score, document = loaded()
        note = first_note(score)
        EditApplier(document).apply(SetTieOp(ref=note.ref, start=True))
        element = document.element(note.ref)
        assert element.find("tie") is not None
        assert element.find("notations/tied") is not None

        reparsed = parse_score(document)
        changed = next(reparsed.parts[0].measures[0].iter_notes())
        assert changed.tie.sounds_start and changed.tie.prints_start
        assert not changed.tie.is_inconsistent

    def test_removing_a_tie_clears_both(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("C4:4~", "~C4:4"))
        note = first_note(score)
        EditApplier(document).apply(SetTieOp(ref=note.ref, start=False))
        element = document.element(note.ref)
        assert element.find("tie") is None
        assert element.find("notations/tied") is None

    def test_slur_add_and_remove(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        note = first_note(score)
        applier = EditApplier(document)
        applier.apply(SetSlurOp(ref=note.ref, action=EditAction.ADD, role=SpannerRole.START))
        assert document.element(note.ref).find("notations/slur") is not None
        applier.apply(SetSlurOp(ref=note.ref, action=EditAction.REMOVE, role=SpannerRole.START))
        assert document.element(note.ref).find("notations/slur") is None


class TestAttributeEdits:
    def test_clef_change(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        clef = score.parts[0].measures[0].attributes.clef_for_staff(1)
        EditApplier(document).apply(SetClefOp(ref=clef.ref, sign="F", line=4))
        reparsed = parse_score(document)
        assert reparsed.parts[0].measures[0].attributes.clef_for_staff(1).describe() == "F4"

    def test_key_change(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        key = score.parts[0].measures[0].attributes.key
        EditApplier(document).apply(SetKeyOp(ref=key.ref, fifths=-3, mode="minor"))
        reparsed = parse_score(document)
        assert reparsed.parts[0].measures[0].attributes.key.fifths == -3

    def test_voice_reassignment(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        note = first_note(score)
        EditApplier(document).apply(SetVoiceOp(ref=note.ref, voice="2"))
        reparsed = parse_score(document)
        assert reparsed.parts[0].measures[0].events[0].voice == "2"


class TestStructuralEdits:
    def test_removing_an_element_is_flagged_structural(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("C4:1 R:1 D4:2"))
        rest = next(score.parts[0].measures[0].iter_rests())
        applier = EditApplier(document)
        applier.apply(RemoveElementOp(ref=rest.ref, reason="test"))
        assert applier.has_structural_changes
        reparsed = parse_score(document)
        assert not list(reparsed.parts[0].measures[0].iter_rests())

    def test_removal_is_undoable(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("C4:1 R:1 D4:2"))
        before = document.to_bytes()
        rest = next(score.parts[0].measures[0].iter_rests())
        applier = EditApplier(document)
        applier.apply(RemoveElementOp(ref=rest.ref, reason="test"))
        assert applier.undo_last()
        assert document.to_bytes() == before


class TestUndo:
    def test_undo_restores_the_exact_bytes(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        before = document.to_bytes()
        note = first_note(score)
        applier = EditApplier(document)
        applier.apply(SetPitchOp(ref=note.ref, step=Step.G, alter=1, octave=5))
        assert document.to_bytes() != before
        applier.undo_last()
        assert document.to_bytes() == before

    def test_undo_all_unwinds_everything(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded()
        before = document.to_bytes()
        notes = list(score.parts[0].measures[0].iter_notes())
        applier = EditApplier(document)
        for note in notes:
            applier.apply(SetPitchOp(ref=note.ref, step=Step.B, alter=0, octave=3))
        assert applier.undo_all() == len(notes)
        assert document.to_bytes() == before

    def test_undo_on_empty_history_is_false(self, loaded) -> None:  # type: ignore[no-untyped-def]
        _, document = loaded()
        assert EditApplier(document).undo_last() is False

    def test_handle_still_resolves_after_undo(self, loaded) -> None:  # type: ignore[no-untyped-def]
        """An undone edit must leave the handle pointing at a live element, or the next edit
        would target a node that is no longer in the tree."""
        score, document = loaded()
        note = first_note(score)
        applier = EditApplier(document)
        applier.apply(SetPitchOp(ref=note.ref, step=Step.G, alter=0, octave=4))
        applier.undo_last()
        element = document.element(note.ref)
        assert element.getroottree().getroot() is document.tree.getroot()
        applier.apply(SetPitchOp(ref=note.ref, step=Step.A, alter=0, octave=4))
        assert parse_score(document).parts[0].measures[0].events[0].pitch.name == "A4"  # type: ignore[union-attr]


class TestFailureHandling:
    def test_unknown_handle_is_rejected(self, loaded) -> None:  # type: ignore[no-untyped-def]
        _, document = loaded()
        with pytest.raises(EditApplicationError, match="not in this document"):
            EditApplier(document).apply(SetPitchOp(ref=99999, step=Step.C, alter=0, octave=4))

    def test_pitch_edit_on_a_rest_is_rejected(self, loaded) -> None:  # type: ignore[no-untyped-def]
        score, document = loaded(single_part("R:4"))
        rest = next(score.parts[0].measures[0].iter_rests())
        with pytest.raises(EditApplicationError, match="not a pitched note"):
            EditApplier(document).apply(SetPitchOp(ref=rest.ref, step=Step.C, alter=0, octave=4))


def test_layout_and_unmodelled_content_survive(tmp_path: Path) -> None:
    """The reason corrections are patches and not a re-serialization: everything we do not model
    has to come back out of the file unchanged."""
    path = tmp_path / "score.musicxml"
    write_musicxml(single_part("C4:1 D4:1 E4:1 F4:1"), str(path))
    tree = etree.parse(str(path))
    root = tree.getroot()
    credit = etree.SubElement(root, "credit", page="1")
    words = etree.SubElement(credit, "credit-words")
    words.text = "Engraved by hand, 1897"
    words.set("default-x", "616")
    root.find(".//measure").set("width", "218.53")
    tree.write(str(path))

    document = SourceDocument.load(path)
    score = parse_score(document)
    note = first_note(score)
    EditApplier(document).apply(SetPitchOp(ref=note.ref, step=Step.D, alter=0, octave=4))

    output = document.to_bytes().decode()
    assert "Engraved by hand, 1897" in output
    assert 'default-x="616"' in output
    assert 'width="218.53"' in output
