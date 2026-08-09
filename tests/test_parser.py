"""Parser tests.

The parser's job is to turn MusicXML's serialization conventions — chord flags, backup/forward
cursor jumps, sticky attributes — into a representation where every event knows when it happens.
These tests are written against that contract rather than against XML shapes.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from ai_proofreader.models import Chord, Note, Rest
from ai_proofreader.score_parser import (
    ScoreLoadError,
    SourceDocument,
    UnsupportedFormatError,
    parse_score,
)
from ai_proofreader.testing import MeasureSpec, PartSpec, ScoreSpec, write_musicxml
from conftest import single_part


def test_parses_pitches_durations_and_onsets(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("C4:1 D4:1/2 E4:1/2 F4:2"))
    events = score.parts[0].measures[0].events
    assert [event.onset for event in events] == [
        Fraction(0),
        Fraction(1),
        Fraction(3, 2),
        Fraction(2),
    ]
    assert [note.pitch.name for note in score.parts[0].measures[0].iter_notes()] == [
        "C4",
        "D4",
        "E4",
        "F4",
    ]


def test_groups_chords_into_one_event(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("[C4,E4,G4]:2 [D4,F4]:2"))
    events = score.parts[0].measures[0].events
    assert all(isinstance(event, Chord) for event in events)
    assert [len(event.notes) for event in events] == [3, 2]  # type: ignore[union-attr]
    assert events[1].onset == 2


def test_chord_members_share_onset_and_duration(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("[C4,E4,G4]:4"))
    chord = score.parts[0].measures[0].events[0]
    assert isinstance(chord, Chord)
    assert {note.onset for note in chord.notes} == {Fraction(0)}
    assert chord.highest.pitch.name == "G4"
    assert chord.lowest.pitch.name == "C4"


def test_backup_puts_voices_at_the_same_time(parsed) -> None:  # type: ignore[no-untyped-def]
    spec = ScoreSpec(
        parts=[
            PartSpec(
                id="P1",
                measures=[MeasureSpec(voices={"1": "C5:2 D5:2", "2": "E4:4"})],
            )
        ]
    )
    score, _ = parsed(spec)
    measure = score.parts[0].measures[0]
    assert measure.voices == ("1", "2")
    assert [event.onset for event in measure.events_in_voice("2")] == [Fraction(0)]
    assert measure.voice_length("1") == 4
    assert measure.voice_length("2") == 4


def test_tuplet_durations_stay_exact(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("C4:1/3 D4:1/3 E4:1/3 F4:1 G4:1 A4:1"))
    measure = score.parts[0].measures[0]
    triplet = measure.events[:3]
    assert sum(event.duration.quarter_length for event in triplet) == 1
    assert triplet[0].duration.time_modification is not None
    assert triplet[0].duration.time_modification.actual_notes == 3
    assert measure.voice_length("1") == 4


def test_ties_carry_both_sounding_and_printed_flags(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("C4:4~", "~C4:4"))
    first = next(score.parts[0].measures[0].iter_notes())
    second = next(score.parts[0].measures[1].iter_notes())
    assert first.tie.starts and first.tie.sounds_start and first.tie.prints_start
    assert second.tie.stops
    assert not first.tie.is_inconsistent


def test_slurs_are_parsed_as_spanner_endpoints(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("(C4:1 D4:1 E4:1 F4:1)"))
    notes = list(score.parts[0].measures[0].iter_notes())
    assert notes[0].slurs[0].role.value == "start"
    assert notes[-1].slurs[0].role.value == "stop"


def test_rests_are_events_not_gaps(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("C4:1 R:1 D4:2"))
    events = score.parts[0].measures[0].events
    assert isinstance(events[1], Rest)
    assert events[2].onset == 2


def test_attributes_are_sticky_across_measures(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("C4:4", "D4:4", "E4:4", key_fifths=3, time=(4, 4)))
    for measure in score.parts[0].measures:
        assert measure.attributes.key.fifths == 3
        assert measure.attributes.time is not None
        assert measure.attributes.time.beat_type == 4


def test_mid_score_key_change_is_recorded(parsed) -> None:  # type: ignore[no-untyped-def]
    spec = ScoreSpec(
        key_fifths=0,
        parts=[
            PartSpec(
                id="P1",
                measures=[
                    MeasureSpec.single("C4:4"),
                    MeasureSpec(voices={"1": "C4:4"}, key_fifths=-3),
                    MeasureSpec.single("C4:4"),
                ],
            )
        ],
    )
    score, _ = parsed(spec)
    assert [measure.attributes.key.fifths for measure in score.parts[0].measures] == [0, -3, -3]


def test_transposition_is_read_from_attributes(parsed) -> None:  # type: ignore[no-untyped-def]
    spec = ScoreSpec(
        parts=[PartSpec(id="P1", transpose=(-1, -2, 0), measures=[MeasureSpec.single("D5:4")])]
    )
    score, _ = parsed(spec)
    part = score.parts[0]
    assert part.is_transposing
    assert part.transpose.apply(part.measures[0].events[0].pitch).name == "C5"  # type: ignore[union-attr]


def test_multi_staff_part_keeps_staff_assignment(parsed) -> None:  # type: ignore[no-untyped-def]
    spec = ScoreSpec(
        parts=[
            PartSpec(
                id="P1",
                staves=2,
                measures=[
                    MeasureSpec(voices={"1": "C5:4", "2": "C3:4"}, staff_of_voice={"1": 1, "2": 2})
                ],
            )
        ]
    )
    score, _ = parsed(spec)
    measure = score.parts[0].measures[0]
    assert score.parts[0].staves == 2
    assert {event.staff for event in measure.events} == {1, 2}
    assert measure.attributes.clef_for_staff(2).sign.value == "F"


def test_every_element_gets_a_handle(parsed) -> None:  # type: ignore[no-untyped-def]
    score, document = parsed(single_part("C4:1 D4:1 E4:1 F4:1"))
    refs = [note.ref for note in score.parts[0].measures[0].iter_notes()]
    assert len(set(refs)) == 4
    for ref in refs:
        assert document.element(ref) is not None
        assert "note" in document.locator(ref)


def test_handles_survive_a_reparse(parsed) -> None:  # type: ignore[no-untyped-def]
    """Re-parsing an unmodified document must produce the same handles, or accepting a
    suggestion after a re-analysis would target the wrong note."""
    score, document = parsed(single_part("C4:1 D4:1 E4:1 F4:1"))
    before = [note.ref for note in score.parts[0].measures[0].iter_notes()]
    again = parse_score(document)
    after = [note.ref for note in again.parts[0].measures[0].iter_notes()]
    assert before == after


def test_metadata_is_extracted(parsed) -> None:  # type: ignore[no-untyped-def]
    spec = ScoreSpec(
        title="A Title",
        composer="A Composer",
        parts=[PartSpec(measures=[MeasureSpec.single("C4:4")])],
    )
    score, _ = parsed(spec)
    assert score.metadata.title == "A Title"
    assert score.metadata.composer == "A Composer"
    assert score.metadata.looks_like_musescore


class TestContainers:
    def test_rejects_unknown_extension(self, tmp_path: Path) -> None:
        target = tmp_path / "score.txt"
        target.write_text("not a score")
        with pytest.raises(UnsupportedFormatError):
            SourceDocument.load(target)

    def test_rejects_non_score_xml(self, tmp_path: Path) -> None:
        target = tmp_path / "other.xml"
        target.write_text("<?xml version='1.0'?><html><body/></html>")
        with pytest.raises(ScoreLoadError, match="score-partwise"):
            SourceDocument.load(target)

    def test_reports_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ScoreLoadError, match="No such file"):
            SourceDocument.load(tmp_path / "absent.musicxml")

    def test_mxl_round_trip(self, tmp_path: Path) -> None:
        plain = tmp_path / "score.musicxml"
        write_musicxml(single_part("C4:1 D4:1 E4:1 F4:1"), str(plain))
        document = SourceDocument.load(plain)
        compressed = document.save(tmp_path / "score.mxl")

        reloaded = SourceDocument.load(compressed)
        assert reloaded.source_format == "mxl"
        score = parse_score(reloaded)
        assert [note.pitch.name for note in score.parts[0].measures[0].iter_notes()] == [
            "C4",
            "D4",
            "E4",
            "F4",
        ]


def test_malformed_measure_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A score out of an OMR engine is exactly the score most likely to be malformed. One bad
    bar must cost that bar, not the whole analysis."""
    path = tmp_path / "broken.musicxml"
    write_musicxml(single_part("C4:1 D4:1 E4:1 F4:1", "G4:4"), str(path))
    text = path.read_text()
    # A duration that is not a number at all — the kind of thing a broken export produces.
    text = text.replace("<duration>96</duration>", "<duration>oops</duration>", 1)
    path.write_text(text)

    score = parse_score(SourceDocument.load(path))
    assert score.measure_count == 2
    assert score.note_count >= 4


def test_grace_notes_take_no_time(parsed) -> None:  # type: ignore[no-untyped-def]
    """Grace notes are parsed but must not displace the beat."""
    score, _ = parsed(single_part("C4:1 D4:1 E4:1 F4:1"))
    measure = score.parts[0].measures[0]
    assert measure.voice_length("1") == 4
    assert all(event.duration.is_measured for event in measure.events)


def test_note_and_measure_counts(parsed) -> None:  # type: ignore[no-untyped-def]
    score, _ = parsed(single_part("[C4,E4]:2 R:2", "C4:4"))
    assert score.note_count == 3
    assert score.measure_count == 2
    assert isinstance(score.parts[0].measures[0].events[0], Chord)
    assert isinstance(score.parts[0].measures[1].events[0], Note)
