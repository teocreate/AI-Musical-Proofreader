"""Detector tests.

Each rule gets two kinds of test: the error it exists to find, and the ordinary music it must
stay silent about. The second kind is the one that matters — a detector with no negative tests is
a detector whose precision nobody has checked.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ai_proofreader.analysis import AnalysisContext
from ai_proofreader.analysis.detectors import (
    AccidentalConsistencyDetector,
    ChromaticOutlierDetector,
    ClefPlausibilityDetector,
    ContourSpikeDetector,
    HarmonicOutlierDetector,
    KeySignatureConsistencyDetector,
    MeasureDurationDetector,
    MotifDeviationDetector,
    SlurStructureDetector,
    TieIntegrityDetector,
    TupletDetector,
    VoiceOverlapDetector,
)
from ai_proofreader.config import AnalysisConfig
from ai_proofreader.models import Suggestion, SuggestionKind
from ai_proofreader.testing import MeasureSpec, PartSpec, ScoreSpec
from conftest import single_part


def run(detector, context: AnalysisContext) -> list[Suggestion]:  # type: ignore[no-untyped-def]
    return list(detector.run(context))


def kinds(suggestions: Sequence[Suggestion]) -> set[SuggestionKind]:
    return {suggestion.kind for suggestion in suggestions}


class TestMeasureDuration:
    def test_full_bar_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(MeasureDurationDetector(), context(single_part("C4:1 D4:1 E4:1 F4:1")))
        assert found == []

    def test_short_bar_with_one_repair_names_it(self, context) -> None:  # type: ignore[no-untyped-def]
        """3.25 beats, short by 0.75. Only one note can absorb that: the quarter, double-dotted.
        Uniqueness is what earns the automatic correction."""
        found = run(MeasureDurationDetector(), context(single_part("C4:2 D4:1 E4:1/4")))
        assert len(found) == 1
        assert found[0].is_actionable
        assert found[0].kind in {SuggestionKind.DOT_ADD, SuggestionKind.DURATION}
        assert "double-dotted" in found[0].suggested_repr

    def test_several_repairs_means_no_automatic_correction(self, context) -> None:  # type: ignore[no-untyped-def]
        """Short by half a beat with two notes that could each absorb it — dotting the quarter or
        doubling the eighth. Both are plausible, so neither is offered."""
        found = run(MeasureDurationDetector(), context(single_part("C4:1 D4:1/2 E4:2")))
        assert len(found) == 1
        assert not found[0].is_actionable

    def test_ambiguous_short_bar_is_advisory_only(self, context) -> None:  # type: ignore[no-untyped-def]
        """Four equal notes short by one beat: any of them could be the wrong one, and guessing
        would be worse than saying so."""
        found = run(MeasureDurationDetector(), context(single_part("C4:1 D4:1 E4:1/2 F4:1/2")))
        assert len(found) == 1
        assert not found[0].is_actionable
        assert "several" in found[0].explanation.lower()

    def test_overfull_bar_is_reported(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(MeasureDurationDetector(), context(single_part("C4:2 D4:2 E4:2")))
        assert len(found) == 1
        assert "longer than" in found[0].evidence[0].summary

    def test_pickup_bar_is_exempt(self, context) -> None:  # type: ignore[no-untyped-def]
        spec = ScoreSpec(
            parts=[
                PartSpec(
                    id="P1",
                    measures=[
                        MeasureSpec(voices={"1": "G4:1"}, implicit=True),
                        MeasureSpec.single("C4:1 D4:1 E4:1 F4:1"),
                    ],
                )
            ]
        )
        assert run(MeasureDurationDetector(), context(spec)) == []

    def test_secondary_voice_may_stop_early(self, context) -> None:  # type: ignore[no-untyped-def]
        """Ordinary notation, not an error — flagging it would fire on every keyboard score."""
        spec = ScoreSpec(
            parts=[
                PartSpec(
                    id="P1",
                    measures=[MeasureSpec(voices={"1": "C5:1 D5:1 E5:1 F5:1", "2": "C4:2"})],
                )
            ]
        )
        assert run(MeasureDurationDetector(), context(spec)) == []

    def test_whole_measure_rest_is_exempt(self, context) -> None:  # type: ignore[no-untyped-def]
        assert run(MeasureDurationDetector(), context(single_part("R:4"))) == []


class TestTieIntegrity:
    def test_valid_tie_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        assert run(TieIntegrityDetector(), context(single_part("C4:4~", "~C4:4"))) == []

    def test_tie_between_different_pitches_is_impossible(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(TieIntegrityDetector(), context(single_part("C4:4~", "~D4:4")))
        assert len(found) == 1
        assert found[0].kind is SuggestionKind.PITCH
        assert found[0].suggested_repr == "C4"
        assert found[0].confidence is not None

    def test_tie_across_an_accidental_difference_picks_the_printed_one(self, context) -> None:  # type: ignore[no-untyped-def]
        """F# tied to F: the note carrying the printed sharp is the reliable one."""
        found = run(TieIntegrityDetector(), context(single_part("F#4:4~", "~F4:4", key_fifths=0)))
        assert len(found) == 1
        assert found[0].suggested_repr == "F#4"
        assert found[0].current_repr == "F4"

    def test_dangling_tie_is_reported(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(TieIntegrityDetector(), context(single_part("C4:2~ R:2")))
        assert len(found) == 1
        assert found[0].kind is SuggestionKind.TIE_REMOVE

    def test_unmarked_continuation_suggests_closing_the_tie(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(TieIntegrityDetector(), context(single_part("C4:2~ C4:2")))
        assert len(found) == 1
        assert found[0].kind is SuggestionKind.TIE_ADD


class TestSlurStructure:
    def test_balanced_slur_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        assert run(SlurStructureDetector(), context(single_part("(C4:1 D4:1 E4:1 F4:1)"))) == []

    def test_unclosed_slur_is_reported(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(SlurStructureDetector(), context(single_part("(C4:1 D4:1 E4:1 F4:1")))
        assert len(found) == 1
        assert "never ends" in found[0].title


class TestVoiceOverlap:
    def test_chord_is_not_an_overlap(self, context) -> None:  # type: ignore[no-untyped-def]
        assert run(VoiceOverlapDetector(), context(single_part("[C4,E4,G4]:4"))) == []

    def test_two_voices_may_sound_together(self, context) -> None:  # type: ignore[no-untyped-def]
        spec = ScoreSpec(
            parts=[PartSpec(id="P1", measures=[MeasureSpec(voices={"1": "C5:4", "2": "E4:4"})])]
        )
        assert run(VoiceOverlapDetector(), context(spec)) == []

    def test_note_running_over_the_next_is_reported(self, score_file) -> None:  # type: ignore[no-untyped-def]
        """Within one voice, onsets follow from durations, so an overlap can only be written with
        a ``<backup>`` — which is exactly how an engine that mis-segmented a system produces one.
        """
        from lxml import etree

        from ai_proofreader.score_parser import SourceDocument, parse_score

        path = score_file(single_part("C4:2 D4:2"))
        tree = etree.parse(str(path))
        measure = tree.getroot().find(".//measure")
        notes = measure.findall("note")
        backup = etree.Element("backup")
        duration = etree.SubElement(backup, "duration")
        duration.text = "24"  # rewind one quarter, so D4 starts while C4 is still sounding
        measure.insert(list(measure).index(notes[1]), backup)
        tree.write(str(path))

        found = run(VoiceOverlapDetector(), AnalysisContext(parse_score(SourceDocument.load(path))))
        assert any(item.kind is SuggestionKind.DURATION for item in found)
        assert "lasts into the next" in found[0].title


class TestAccidentalConsistency:
    def test_consistent_file_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        assert (
            run(AccidentalConsistencyDetector(), context(single_part("F#4:2 F#4:2", key_fifths=2)))
            == []
        )

    def test_bare_note_contradicting_the_key_is_caught(self, context) -> None:  # type: ignore[no-untyped-def]
        """In D major an unadorned F on the staff sounds F-sharp. A file claiming F natural with
        no printed natural is internally inconsistent whatever the page says."""
        spec = ScoreSpec(
            key_fifths=2,
            parts=[PartSpec(id="P1", measures=[MeasureSpec.single("D4:2 F4:2")])],
        )
        # The builder engraves a natural for the F, so remove it to simulate the broken export.
        context_object = context(spec)
        for measure in context_object.score.parts[0].measures:
            for note in measure.iter_notes():
                if note.pitch.step.value == "F":
                    assert note.accidental is not None  # sanity: the fixture is well-formed
        assert run(AccidentalConsistencyDetector(), context_object) == []


class TestMotifDeviation:
    def make(self, third_statement: str) -> ScoreSpec:
        motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
        filler = "A4:1 B4:1 C#5:1 D5:1"
        return ScoreSpec(
            key_fifths=2,
            parts=[
                PartSpec(
                    id="P1",
                    measures=[
                        MeasureSpec.single(pattern)
                        for pattern in (
                            motif,
                            filler,
                            motif,
                            filler,
                            third_statement,
                            filler,
                            motif,
                            "D5:4",
                        )
                    ],
                )
            ],
        )

    def test_identical_statements_are_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
        assert run(MotifDeviationDetector(), context(self.make(motif))) == []

    def test_one_semitone_deviation_is_found(self, context) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        found = run(MotifDeviationDetector(), context(self.make(broken)))
        assert len(found) == 1
        assert found[0].current_repr == "F5"
        assert found[0].suggested_repr == "F#5"
        assert found[0].target.measure_index == 4
        assert "m. 1" in found[0].explanation

    def test_one_step_deviation_is_found(self, context) -> None:  # type: ignore[no-untyped-def]
        broken = "D5:1/2 E5:1/2 G5:1 E5:1 D5:1"
        found = run(MotifDeviationDetector(), context(self.make(broken)))
        assert len(found) == 1
        assert found[0].suggested_repr == "F#5"
        assert found[0].kind is SuggestionKind.PITCH

    def test_too_few_statements_prove_nothing(self, context) -> None:  # type: ignore[no-untyped-def]
        motif = "D5:1/2 E5:1/2 F#5:1 E5:1 D5:1"
        broken = "D5:1/2 E5:1/2 F5:1 E5:1 D5:1"
        spec = ScoreSpec(
            key_fifths=2,
            parts=[
                PartSpec(
                    id="P1",
                    measures=[MeasureSpec.single(pattern) for pattern in (motif, broken)],
                )
            ],
        )
        config = AnalysisConfig(motif_min_occurrences=3)
        assert run(MotifDeviationDetector(), context(spec, config)) == []


class TestClefPlausibility:
    def test_ordinary_register_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        patterns = ["G4:1 A4:1 B4:1 C5:1"] * 4
        assert run(ClefPlausibilityDetector(), context(single_part(*patterns))) == []

    def test_whole_part_off_the_staff_is_flagged(self, context) -> None:  # type: ignore[no-untyped-def]
        """Bass-register music under a treble clef: the shape of a misread clef glyph."""
        patterns = ["C2:1 E2:1 G2:1 C3:1"] * 5
        found = run(ClefPlausibilityDetector(), context(single_part(*patterns)))
        assert len(found) == 1
        assert found[0].kind is SuggestionKind.CLEF
        assert "F4" in found[0].suggested_repr


class TestKeySignatureConsistency:
    def test_matching_signature_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        patterns = ["D5:1 E5:1 F#5:1 G5:1"] * 10
        assert (
            run(KeySignatureConsistencyDetector(), context(single_part(*patterns, key_fifths=2)))
            == []
        )

    def test_every_f_sharpened_by_hand_means_a_lost_signature(self, context) -> None:  # type: ignore[no-untyped-def]
        patterns = ["D5:1 F#5:1 A5:1 F#5:1"] * 10
        found = run(
            KeySignatureConsistencyDetector(), context(single_part(*patterns, key_fifths=0))
        )
        assert len(found) == 1
        assert found[0].kind is SuggestionKind.KEY_SIGNATURE


class TestHarmonicOutlier:
    def test_plain_triads_are_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        spec = ScoreSpec(
            parts=[
                PartSpec(id="P1", measures=[MeasureSpec.single("C5:4")]),
                PartSpec(id="P2", measures=[MeasureSpec.single("E4:4")]),
                PartSpec(id="P3", measures=[MeasureSpec.single("G3:4")]),
            ]
        )
        assert run(HarmonicOutlierDetector(), context(spec)) == []

    def test_one_note_out_of_the_chord_is_repairable(self, context) -> None:  # type: ignore[no-untyped-def]
        spec = ScoreSpec(
            parts=[
                PartSpec(id="P1", measures=[MeasureSpec.single("C5:4")]),
                PartSpec(id="P2", measures=[MeasureSpec.single("F#4:4")]),
                PartSpec(id="P3", measures=[MeasureSpec.single("G3:4")]),
            ]
        )
        found = run(HarmonicOutlierDetector(), context(spec))
        assert found
        assert any(item.suggested_repr in {"E4", "F4", "G4"} for item in found)


class TestChromaticOutlier:
    def test_diatonic_music_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        patterns = ["G4:1 A4:1 B4:1 C5:1", "D5:1 E5:1 F#5:1 G5:1"] * 3
        assert run(ChromaticOutlierDetector(), context(single_part(*patterns, key_fifths=1))) == []


class TestTuplets:
    def test_complete_triplet_is_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(TupletDetector(), context(single_part("C4:1/3 D4:1/3 E4:1/3 F4:1 G4:1 A4:1")))
        assert found == []

    def test_incomplete_triplet_is_reported(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(TupletDetector(), context(single_part("C4:1/3 D4:1/3 F4:1 G4:1 A4:1 B4:1/3")))
        assert any(item.kind is SuggestionKind.TUPLET for item in found)


class TestContourSpike:
    def test_scales_are_silent(self, context) -> None:  # type: ignore[no-untyped-def]
        assert (
            run(
                ContourSpikeDetector(),
                context(single_part("C4:1/2 D4:1/2 E4:1/2 F4:1/2 G4:1/2 A4:1/2 B4:1/2 C5:1/2")),
            )
            == []
        )

    def test_displaced_note_is_reported(self, context) -> None:  # type: ignore[no-untyped-def]
        found = run(
            ContourSpikeDetector(),
            context(single_part("C4:1/2 D4:1/2 E4:1/2 F4:1/2 B4:1/2 A4:1/2 B4:1/2 C5:1/2")),
        )
        assert any(item.kind is SuggestionKind.PITCH for item in found)


@pytest.mark.parametrize(
    "detector_type",
    [
        MeasureDurationDetector,
        TieIntegrityDetector,
        SlurStructureDetector,
        VoiceOverlapDetector,
        AccidentalConsistencyDetector,
        MotifDeviationDetector,
        ContourSpikeDetector,
        HarmonicOutlierDetector,
        ChromaticOutlierDetector,
        ClefPlausibilityDetector,
        KeySignatureConsistencyDetector,
        TupletDetector,
    ],
)
def test_detector_never_mutates_the_context(detector_type, context) -> None:  # type: ignore[no-untyped-def]
    """The whole parallelism story depends on this."""
    ctx = context(single_part("C4:1 D4:1/2 E4:2", "F#4:4~", "~F4:4"))
    before = ctx.score.model_dump_json()
    run(detector_type(), ctx)
    assert ctx.score.model_dump_json() == before
