"""Tests for the internal representation."""

from __future__ import annotations

from fractions import Fraction

import pytest

from ai_proofreader.models import (
    Accidental,
    Clef,
    ClefSign,
    Duration,
    KeySignature,
    Mode,
    Note,
    Pitch,
    Step,
    TimeSignature,
    Transpose,
    to_rational,
)


class TestPitch:
    @pytest.mark.parametrize(
        ("name", "midi", "diatonic"),
        [
            ("C4", 60, 28),
            ("C-1", 0, -7),
            ("F#4", 66, 31),
            ("Gb4", 66, 32),  # same sound, different staff position
            ("B#3", 60, 27),  # same sound as C4, a line lower
            ("A0", 21, 5),
        ],
    )
    def test_coordinates(self, name: str, midi: int, diatonic: int) -> None:
        pitch = Pitch.from_name(name)
        assert pitch.midi == midi
        assert pitch.diatonic == diatonic

    def test_enharmonics_share_sound_not_position(self) -> None:
        sharp = Pitch.from_name("F#4")
        flat = Pitch.from_name("Gb4")
        assert sharp.is_enharmonic(flat)
        assert sharp.diatonic != flat.diatonic

    def test_step_shift_moves_staff_position(self) -> None:
        """The misread-notehead fix: same alteration policy, adjacent line."""
        assert Pitch.from_name("B4").step_shifted(-1, alter=0).name == "A4"
        assert Pitch.from_name("B4").step_shifted(1, alter=0).name == "C5"

    def test_with_alter_keeps_staff_position(self) -> None:
        """The missing-accidental fix: same line, different sound."""
        original = Pitch.from_name("F4")
        changed = original.with_alter(1)
        assert changed.diatonic == original.diatonic
        assert changed.midi == original.midi + 1

    def test_round_trips_through_name(self) -> None:
        for name in ("C4", "F#4", "Bb3", "Cbb5", "Fx4"):
            assert Pitch.from_name(name).midi == Pitch.from_name(Pitch.from_name(name).name).midi

    def test_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            Pitch.from_name("H4")
        with pytest.raises(ValueError):
            Pitch.from_name("C")


class TestKeySignature:
    @pytest.mark.parametrize(
        ("fifths", "step", "alter"),
        [(0, Step.F, 0), (1, Step.F, 1), (2, Step.C, 1), (-1, Step.B, -1), (-2, Step.E, -1)],
    )
    def test_alterations(self, fifths: int, step: Step, alter: int) -> None:
        assert KeySignature(fifths=fifths).alter_for(step) == alter

    def test_sharps_arrive_in_order(self) -> None:
        assert set(KeySignature(fifths=3).altered_steps) == {Step.F, Step.C, Step.G}

    def test_minor_names_the_relative(self) -> None:
        assert KeySignature(fifths=0, mode=Mode.MINOR).tonic_name == "A"
        assert KeySignature(fifths=3, mode=Mode.MINOR).tonic_name == "F#"


class TestClef:
    @pytest.mark.parametrize(
        ("sign", "line", "bottom"),
        [("G", 2, "E4"), ("F", 4, "G2"), ("C", 3, "F3")],
    )
    def test_bottom_line(self, sign: str, line: int, bottom: str) -> None:
        clef = Clef(sign=ClefSign(sign), line=line)
        assert clef.bottom_line_diatonic == Pitch.from_name(bottom).diatonic

    def test_ledger_lines(self) -> None:
        treble = Clef(sign=ClefSign.G, line=2)
        assert treble.ledger_lines(Pitch.from_name("G4")) == 0
        assert treble.ledger_lines(Pitch.from_name("C4")) == 1
        assert treble.ledger_lines(Pitch.from_name("A5")) == 1
        assert treble.ledger_lines(Pitch.from_name("A3")) == 2

    def test_octave_clef_shifts_everything(self) -> None:
        normal = Clef(sign=ClefSign.G, line=2)
        octave_down = Clef(sign=ClefSign.G, line=2, octave_change=-1)
        assert octave_down.bottom_line_diatonic == normal.bottom_line_diatonic - 7


class TestTimeSignature:
    @pytest.mark.parametrize(
        ("beats", "beat_type", "length"),
        [("4", 4, 4), ("3", 4, 3), ("6", 8, 3), ("2", 2, 4), ("7", 8, Fraction(7, 2))],
    )
    def test_measure_length(self, beats: str, beat_type: int, length: object) -> None:
        assert TimeSignature(beats=beats, beat_type=beat_type).measure_length == length

    def test_additive_signature(self) -> None:
        assert TimeSignature(beats="3+2", beat_type=8).measure_length == Fraction(5, 2)

    def test_compound_beat_unit_is_dotted(self) -> None:
        assert TimeSignature(beats="6", beat_type=8).beat_unit == Fraction(3, 2)
        assert TimeSignature(beats="4", beat_type=4).beat_unit == Fraction(1)


class TestTranspose:
    def test_bflat_instrument_sounds_a_tone_lower(self) -> None:
        clarinet = Transpose(diatonic=-1, chromatic=-2)
        assert clarinet.apply(Pitch.from_name("C5")).name == "Bb4"
        assert clarinet.apply(Pitch.from_name("D5")).name == "C5"

    def test_identity_is_a_no_op(self) -> None:
        pitch = Pitch.from_name("F#4")
        assert Transpose().apply(pitch) is pitch

    def test_octave_change(self) -> None:
        piccolo = Transpose(octave_change=1)
        assert piccolo.apply(Pitch.from_name("C4")).name == "C5"


class TestRational:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("3/2", Fraction(3, 2)),
            (2, Fraction(2)),
            (0.5, Fraction(1, 2)),
            ((1, 3), Fraction(1, 3)),
        ],
    )
    def test_coercion(self, value: object, expected: Fraction) -> None:
        assert to_rational(value) == expected

    def test_rejects_bool(self) -> None:
        with pytest.raises(TypeError):
            to_rational(True)

    def test_serializes_as_string(self) -> None:
        duration = Duration(quarter_length=Fraction(1, 3), ticks=8)
        assert '"1/3"' in duration.model_dump_json()

    def test_triplet_stays_exact(self) -> None:
        third = to_rational(1 / 3)
        assert third * 3 == 1


class TestNote:
    def test_printed_accidental_is_independent_of_sound(self) -> None:
        note = Note(
            onset=Fraction(0),
            duration=Duration(quarter_length=Fraction(1), ticks=4),
            pitch=Pitch.from_name("F#4"),
            accidental=None,
        )
        assert note.pitch.alter == 1
        assert not note.has_printed_accidental

    def test_describe_mentions_the_accidental_when_printed(self) -> None:
        note = Note(
            onset=Fraction(0),
            duration=Duration(quarter_length=Fraction(1), note_type="quarter", ticks=4),
            pitch=Pitch.from_name("F#4"),
            accidental=Accidental.SHARP,
        )
        assert "#" in note.describe()
