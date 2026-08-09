"""Tests for the theory primitives the detectors reason with."""

from __future__ import annotations

from fractions import Fraction

import pytest

from ai_proofreader.models import Accidental, KeySignature, Mode, Note, Pitch
from ai_proofreader.models.events import Duration
from ai_proofreader.music_theory import (
    AccidentalState,
    accidental_for_change,
    chord_fit,
    diatonic_pitch_classes,
    dissonance,
    dot_variants,
    duration_to_type,
    estimate_key,
    expected_alter,
    identify_chord,
    interval_between,
    is_notatable,
    is_spike_shaped,
    local_roughness,
    melodic_difficulty,
    pitch_class_profile,
    spike_analysis,
    try_duration_to_type,
)


def note(name: str, accidental: Accidental | None = None, staff: int = 1) -> Note:
    return Note(
        onset=Fraction(0),
        duration=Duration(quarter_length=Fraction(1), ticks=4),
        pitch=Pitch.from_name(name),
        accidental=accidental,
        staff=staff,
    )


class TestIntervals:
    @pytest.mark.parametrize(
        ("low", "high", "abbreviation"),
        [
            ("C4", "E4", "M3"),
            ("C4", "Eb4", "m3"),
            ("C4", "F#4", "A4"),
            ("B3", "F4", "d5"),
            ("C4", "C5", "P8"),
            ("C4", "D5", "M9"),
            ("C4", "G4", "P5"),
        ],
    )
    def test_spelled_quality(self, low: str, high: str, abbreviation: str) -> None:
        interval = interval_between(Pitch.from_name(low), Pitch.from_name(high))
        assert interval.abbreviation == abbreviation

    def test_direction_is_signed(self) -> None:
        down = interval_between(Pitch.from_name("C5"), Pitch.from_name("A4"))
        assert down.direction == -1
        assert down.abbreviation == "-m3"

    def test_augmented_intervals_are_expensive(self) -> None:
        tritone = interval_between(Pitch.from_name("C4"), Pitch.from_name("F#4"))
        third = interval_between(Pitch.from_name("C4"), Pitch.from_name("E4"))
        assert melodic_difficulty(tritone) > melodic_difficulty(third)

    def test_steps_are_free(self) -> None:
        step = interval_between(Pitch.from_name("C4"), Pitch.from_name("D4"))
        assert melodic_difficulty(step) == 0.0

    def test_augmented_second_is_forgiven(self) -> None:
        """Harmonic minor is full of them; treating them as errors would be useless."""
        augmented_second = interval_between(Pitch.from_name("F4"), Pitch.from_name("G#4"))
        augmented_fourth = interval_between(Pitch.from_name("F4"), Pitch.from_name("B4"))
        assert melodic_difficulty(augmented_second) < melodic_difficulty(augmented_fourth)


class TestKeyEstimation:
    def test_finds_the_obvious_key(self) -> None:
        pitches = [
            Pitch.from_name(n)
            for n in ["G4", "A4", "B4", "C5", "D5", "E5", "F#5", "G5", "D4", "G4"]
        ]
        estimate = estimate_key(pitch_class_profile(pitches))
        assert estimate.name == "G major"
        assert estimate.is_reliable

    def test_minor_is_distinguished_from_its_relative(self) -> None:
        pitches = [
            Pitch.from_name(n)
            for n in ["A4", "B4", "C5", "D5", "E5", "F5", "G#5", "A5", "E4", "A4", "A4"]
        ]
        estimate = estimate_key(pitch_class_profile(pitches))
        assert estimate.mode is Mode.MINOR
        assert estimate.tonic_pitch_class == 9

    def test_chromatic_material_is_not_reliable(self) -> None:
        """The rules that depend on a key must be able to tell when there is not one."""
        pitches = [
            Pitch.from_name(n)
            for n in ["C4", "C#4", "D4", "D#4", "E4", "F4", "F#4", "G4", "G#4", "A4", "A#4", "B4"]
        ]
        assert not estimate_key(pitch_class_profile(pitches)).is_reliable

    def test_empty_profile_falls_back_to_the_prior(self) -> None:
        estimate = estimate_key(pitch_class_profile([]), KeySignature(fifths=2))
        assert estimate.tonic_pitch_class == KeySignature(fifths=2).tonic_pitch_class

    def test_minor_admits_raised_sixth_and_seventh(self) -> None:
        classes = diatonic_pitch_classes(9, Mode.MINOR)  # A minor
        assert Pitch.from_name("G#4").pitch_class in classes
        assert Pitch.from_name("F#4").pitch_class in classes
        assert Pitch.from_name("C#4").pitch_class not in classes


class TestHarmony:
    @pytest.mark.parametrize(
        ("classes", "label"),
        [
            ({0, 4, 7}, "C major triad"),
            ({0, 3, 7}, "C minor triad"),
            ({7, 11, 2, 5}, "G dominant seventh"),
            ({11, 2, 5, 9}, "B half-diminished seventh"),
        ],
    )
    def test_identification(self, classes: set[int], label: str) -> None:
        match = identify_chord(classes)
        assert match is not None
        assert match.label == label

    def test_consonance_ordering(self) -> None:
        assert dissonance({0, 4, 7}) < dissonance({0, 1, 6})

    def test_semitone_repair_improves_fit(self) -> None:
        """The exact question the harmonic detector asks."""
        broken = chord_fit({0, 4, 6})  # C E F#
        fixed = chord_fit({0, 4, 7})  # C E G
        assert fixed > broken

    def test_single_note_is_not_a_chord(self) -> None:
        assert identify_chord({5}) is None


class TestAccidentalSemantics:
    def test_accidental_holds_for_the_rest_of_the_bar(self) -> None:
        state = AccidentalState(key=KeySignature(fifths=0))
        first = note("F#4", Accidental.SHARP)
        assert expected_alter(state, first) == 1
        state.observe(1, first.pitch, first.accidental)
        # A later bare F on the same line is still sharp.
        assert expected_alter(state, note("F4")) == 1

    def test_carry_over_does_not_cross_octaves(self) -> None:
        state = AccidentalState(key=KeySignature(fifths=0))
        state.observe(1, Pitch.from_name("F#4"), Accidental.SHARP)
        assert expected_alter(state, note("F5")) == 0

    def test_key_signature_supplies_the_default(self) -> None:
        state = AccidentalState(key=KeySignature(fifths=2))
        assert expected_alter(state, note("F4")) == 1
        assert expected_alter(state, note("G4")) == 0

    def test_natural_cancels(self) -> None:
        state = AccidentalState(key=KeySignature(fifths=2))
        natural = note("F4", Accidental.NATURAL)
        assert expected_alter(state, natural) == 0
        state.observe(1, natural.pitch, natural.accidental)
        assert expected_alter(state, note("F4")) == 0

    def test_accidental_for_change(self) -> None:
        assert accidental_for_change(0, 1) is Accidental.SHARP
        assert accidental_for_change(1, 0) is Accidental.NATURAL
        assert accidental_for_change(1, 1) is None


class TestRhythm:
    @pytest.mark.parametrize(
        ("quarters", "expected"),
        [
            (Fraction(1), ("quarter", 0, None)),
            (Fraction(3, 2), ("quarter", 1, None)),
            (Fraction(7, 4), ("quarter", 2, None)),
            (Fraction(4), ("whole", 0, None)),
            (Fraction(1, 3), ("eighth", 0, (3, 2))),
        ],
    )
    def test_duration_inference(self, quarters: Fraction, expected: tuple) -> None:
        assert duration_to_type(quarters) == expected

    def test_unwritable_duration_is_rejected(self) -> None:
        assert try_duration_to_type(Fraction(5, 7)) is None
        assert not is_notatable(Fraction(5, 7))

    def test_dot_variants_give_exact_lengths(self) -> None:
        variants = dict(dot_variants(Fraction(1), 0))
        assert variants[1] == Fraction(3, 2)
        assert variants[2] == Fraction(7, 4)

    def test_dot_variants_work_from_a_dotted_note(self) -> None:
        variants = dict(dot_variants(Fraction(3, 2), 1))
        assert variants[0] == Fraction(1)


class TestContour:
    def line(self, text: str) -> list[Pitch]:
        return [Pitch.from_name(name) for name in text.split()]

    def test_smooth_line_has_no_spikes(self) -> None:
        pitches = self.line("C4 D4 E4 F4 G4 A4 B4 C5")
        assert all(spike_analysis(pitches, index) is None for index in range(len(pitches)))

    def test_displaced_note_is_found_and_repaired(self) -> None:
        pitches = self.line("C4 D4 E4 F4 B4 A4 B4 C5")
        anomaly = spike_analysis(pitches, 4)
        assert anomaly is not None
        assert anomaly.step_offset == -2
        assert anomaly.improvement > 0.9

    def test_phrase_opening_leap_is_not_a_spike(self) -> None:
        """Leaping somewhere new and staying there is ordinary music, not an error."""
        assert not is_spike_shaped(
            Pitch.from_name("A4"), Pitch.from_name("F#4"), Pitch.from_name("E5")
        )

    def test_leap_and_return_is_a_spike(self) -> None:
        assert is_spike_shaped(Pitch.from_name("F4"), Pitch.from_name("B4"), Pitch.from_name("G4"))

    def test_endpoints_are_never_spikes(self) -> None:
        pitches = self.line("C4 D4 E4")
        assert spike_analysis(pitches, 0) is None
        assert spike_analysis(pitches, 2) is None

    def test_local_roughness_only_counts_adjacent_intervals(self) -> None:
        pitches = self.line("C4 D4 E4 F4")
        assert local_roughness(pitches, 1) == pytest.approx(0.0)
