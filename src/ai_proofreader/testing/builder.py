"""A compact MusicXML writer, used to build test fixtures and the regression corpus.

Writing MusicXML by hand in test files is unreadable and writing it with an OMR engine is
circular, so tests describe music in a one-line pattern language::

    "C4:1 D4:1 E4:1/2 R:1/2 [C4,E4,G4]:1 F#4:1~ F#4:1"

* ``C4``, ``F#4``, ``Bb3`` — a pitch (``x`` doubles the sharp).
* ``R`` — a rest.
* ``[C4,E4,G4]`` — a chord.
* ``:1``, ``:1/2``, ``:3/2`` — duration in quarter notes, exact rationals.
* trailing ``~`` — start a tie into the next event; leading ``~`` — stop one.
* ``(`` / ``)`` around a run — a slur; the closing paren may sit either side of the duration.
* ``.`` suffix on a pitch — staccato.

Note *type* and tuplet ratios are derived from the duration, so ``:1/3`` produces a proper
triplet eighth with ``<time-modification>``. That inference is the only clever part of this
module and it is unit-tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from lxml import etree

from ..models import KeySignature, Step
from ..music_theory.rhythm import duration_to_type

__all__ = [
    "MeasureSpec",
    "NoteSpec",
    "PartSpec",
    "ScoreSpec",
    "build_musicxml",
    "duration_to_type",
    "parse_pattern",
    "write_musicxml",
]

_TOKEN_RE = re.compile(
    r"""
    ^(?P<prefix>[(~]*)
    (?:
        \[(?P<chord>[^\]]+)\]
      | (?P<pitch>[A-Ga-g](?:\#{1,2}|b{1,2}|x)?-?\d)
      | (?P<rest>[Rr])
    )
    (?P<mid>[~).]*)
    (?::(?P<duration>\d+(?:/\d+)?))?
    (?P<post>[~).]*)$
    """,
    re.VERBOSE,
)

_PITCH_RE = re.compile(r"^([A-Ga-g])(\#{1,2}|b{1,2}|x)?(-?\d)$")


@dataclass
class NoteSpec:
    """One event in a voice."""

    pitches: tuple[str, ...] = ()  # empty = rest
    quarters: Fraction = Fraction(1)
    tie_start: bool = False
    tie_stop: bool = False
    slur_start: bool = False
    slur_stop: bool = False
    staccato: bool = False
    accidental: str | None = None  # forced printed accidental
    grace: bool = False

    @property
    def is_rest(self) -> bool:
        return not self.pitches


@dataclass
class MeasureSpec:
    """One measure of one part. ``voices`` maps a voice id to a pattern string."""

    voices: dict[str, str] = field(default_factory=dict)
    key_fifths: int | None = None
    mode: str | None = None
    time: tuple[int, int] | None = None
    clef: tuple[str, int] | None = None
    staff_of_voice: dict[str, int] = field(default_factory=dict)
    dynamics: dict[str, str] = field(default_factory=dict)  # onset "0" -> "mf"
    repeat_forward: bool = False
    repeat_backward: bool = False
    implicit: bool = False

    @classmethod
    def single(cls, pattern: str, **kwargs: object) -> MeasureSpec:
        return cls(voices={"1": pattern}, **kwargs)  # type: ignore[arg-type]


@dataclass
class PartSpec:
    id: str = "P1"
    name: str = "Piano"
    abbreviation: str = ""
    staves: int = 1
    transpose: tuple[int, int, int] | None = None  # diatonic, chromatic, octave-change
    #: Written key signature for this part. Transposing instruments carry their own — a B-flat
    #: clarinet playing in concert F major reads a part written in G major — and MusicXML stores
    #: the written one.
    key_fifths: int | None = None
    measures: list[MeasureSpec] = field(default_factory=list)


@dataclass
class ScoreSpec:
    title: str = "Test Score"
    composer: str = ""
    software: str = "MuseScore Studio 4.4"
    divisions: int = 24  # divisible by 8 and 3: covers 32nds and triplets exactly
    key_fifths: int = 0
    mode: str = "major"
    time: tuple[int, int] = (4, 4)
    clef: tuple[str, int] = ("G", 2)
    parts: list[PartSpec] = field(default_factory=list)


def parse_pitch(token: str) -> tuple[str, int, int]:
    """``"F#4"`` → ``("F", 1, 4)`` as (step, alter, octave)."""
    match = _PITCH_RE.match(token.strip())
    if not match:
        raise ValueError(f"bad pitch token {token!r}")
    step, accidental, octave = match.groups()
    alter = 0
    if accidental:
        if accidental == "x":
            alter = 2
        elif accidental[0] == "#":
            alter = len(accidental)
        else:
            alter = -len(accidental)
    return step.upper(), alter, int(octave)


def parse_pattern(pattern: str) -> list[NoteSpec]:
    """Parse a whole pattern string into events."""
    specs: list[NoteSpec] = []
    for raw in pattern.split():
        match = _TOKEN_RE.match(raw)
        if not match:
            raise ValueError(f"bad pattern token {raw!r}")
        groups = match.groupdict()
        prefix = groups["prefix"] or ""
        suffix = (groups["mid"] or "") + (groups["post"] or "")
        duration = Fraction(groups["duration"]) if groups["duration"] else Fraction(1)
        if groups["chord"]:
            pitches = tuple(p.strip() for p in groups["chord"].split(",") if p.strip())
        elif groups["pitch"]:
            pitches = (groups["pitch"],)
        else:
            pitches = ()
        specs.append(
            NoteSpec(
                pitches=pitches,
                quarters=duration,
                tie_start="~" in suffix,
                tie_stop="~" in prefix,
                slur_start="(" in prefix,
                slur_stop=")" in suffix,
                staccato="." in suffix,
            )
        )
    return specs


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    element = etree.SubElement(parent, tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if text is not None:
        element.text = text
    return element


_ACCIDENTAL_NAMES: dict[int, str] = {
    -2: "flat-flat",
    -1: "flat",
    0: "natural",
    1: "sharp",
    2: "double-sharp",
}


def _accidental_for(
    pitch_token: str,
    key: KeySignature,
    in_force: dict[tuple[int, str, int], int],
    staff: int,
) -> str | None:
    """Which accidental an engraver would print for this note, updating what is in force."""
    step, alter, octave = parse_pitch(pitch_token)
    signature = (staff, step, octave)
    current = in_force.get(signature, key.alter_for(Step(step)))
    if alter == current:
        return None
    in_force[signature] = alter
    return _ACCIDENTAL_NAMES.get(alter)


def _write_note(
    measure: etree._Element,
    spec: NoteSpec,
    *,
    divisions: int,
    voice: str,
    staff: int | None,
    chord_index: int = 0,
    pitch_token: str | None = None,
    accidental: str | None = None,
) -> None:
    note = _sub(measure, "note")
    if chord_index > 0:
        _sub(note, "chord")

    if spec.is_rest:
        _sub(note, "rest")
    else:
        step, alter, octave = parse_pitch(pitch_token or spec.pitches[0])
        pitch = _sub(note, "pitch")
        _sub(pitch, "step", step)
        if alter:
            _sub(pitch, "alter", str(alter))
        _sub(pitch, "octave", str(octave))

    note_type, dots, tuplet = duration_to_type(spec.quarters)
    ticks = spec.quarters * divisions
    if ticks.denominator != 1:
        raise ValueError(
            f"duration {spec.quarters} is not representable with divisions={divisions}"
        )
    _sub(note, "duration", str(int(ticks)))

    if not spec.is_rest and spec.tie_stop:
        _sub(note, "tie", type="stop")
    if not spec.is_rest and spec.tie_start:
        _sub(note, "tie", type="start")

    _sub(note, "voice", voice)
    _sub(note, "type", note_type)
    for _ in range(dots):
        _sub(note, "dot")
    engraved = spec.accidental or accidental
    if engraved and not spec.is_rest:
        _sub(note, "accidental", engraved)
    if tuplet:
        modification = _sub(note, "time-modification")
        _sub(modification, "actual-notes", str(tuplet[0]))
        _sub(modification, "normal-notes", str(tuplet[1]))
    if staff is not None:
        _sub(note, "staff", str(staff))

    notations_needed = (
        ((spec.tie_start or spec.tie_stop) and not spec.is_rest)
        or spec.slur_start
        or spec.slur_stop
        or spec.staccato
    )
    if notations_needed:
        notations = _sub(note, "notations")
        if not spec.is_rest and spec.tie_stop:
            _sub(notations, "tied", type="stop")
        if not spec.is_rest and spec.tie_start:
            _sub(notations, "tied", type="start")
        if spec.slur_start:
            _sub(notations, "slur", number="1", type="start")
        if spec.slur_stop:
            _sub(notations, "slur", number="1", type="stop")
        if spec.staccato:
            articulations = _sub(notations, "articulations")
            _sub(articulations, "staccato")


def build_musicxml(spec: ScoreSpec) -> etree._ElementTree:
    """Render a :class:`ScoreSpec` to a MusicXML element tree."""
    root = etree.Element("score-partwise", version="4.0")

    work = _sub(root, "work")
    _sub(work, "work-title", spec.title)
    identification = _sub(root, "identification")
    if spec.composer:
        _sub(identification, "creator", spec.composer, type="composer")
    encoding = _sub(identification, "encoding")
    _sub(encoding, "software", spec.software)
    _sub(encoding, "encoding-date", "2026-01-01")

    part_list = _sub(root, "part-list")
    for part_spec in spec.parts:
        score_part = _sub(part_list, "score-part", id=part_spec.id)
        _sub(score_part, "part-name", part_spec.name)
        if part_spec.abbreviation:
            _sub(score_part, "part-abbreviation", part_spec.abbreviation)

    for part_spec in spec.parts:
        part = _sub(root, "part", id=part_spec.id)
        fifths = part_spec.key_fifths if part_spec.key_fifths is not None else spec.key_fifths
        for index, measure_spec in enumerate(part_spec.measures):
            if measure_spec.key_fifths is not None:
                fifths = measure_spec.key_fifths
            measure = _sub(part, "measure", number=str(index + 1))
            if measure_spec.implicit:
                measure.set("implicit", "yes")
            _write_measure(measure, measure_spec, spec, part_spec, first=index == 0, fifths=fifths)

    return etree.ElementTree(root)


def _write_measure(
    measure: etree._Element,
    measure_spec: MeasureSpec,
    spec: ScoreSpec,
    part_spec: PartSpec,
    *,
    first: bool,
    fifths: int = 0,
) -> None:
    needs_attributes = (
        first
        or measure_spec.key_fifths is not None
        or measure_spec.time is not None
        or measure_spec.clef is not None
    )
    if needs_attributes:
        attributes = _sub(measure, "attributes")
        if first:
            _sub(attributes, "divisions", str(spec.divisions))
        written_key = measure_spec.key_fifths
        if written_key is None and first:
            written_key = (
                part_spec.key_fifths if part_spec.key_fifths is not None else spec.key_fifths
            )
        if written_key is not None:
            key = _sub(attributes, "key")
            _sub(key, "fifths", str(written_key))
            _sub(key, "mode", measure_spec.mode or spec.mode)
        time = measure_spec.time or (spec.time if first else None)
        if time is not None:
            time_element = _sub(attributes, "time")
            _sub(time_element, "beats", str(time[0]))
            _sub(time_element, "beat-type", str(time[1]))
        if first and part_spec.staves > 1:
            _sub(attributes, "staves", str(part_spec.staves))
        clefs = [measure_spec.clef] if measure_spec.clef else ([spec.clef] if first else [])
        if first and part_spec.staves > 1 and not measure_spec.clef:
            clefs = [("G", 2), ("F", 4)][: part_spec.staves]
        for staff_index, clef in enumerate(clefs, start=1):
            clef_element = _sub(attributes, "clef")
            if part_spec.staves > 1:
                clef_element.set("number", str(staff_index))
            _sub(clef_element, "sign", clef[0])
            _sub(clef_element, "line", str(clef[1]))
        if first and part_spec.transpose:
            transpose = _sub(attributes, "transpose")
            _sub(transpose, "diatonic", str(part_spec.transpose[0]))
            _sub(transpose, "chromatic", str(part_spec.transpose[1]))
            if part_spec.transpose[2]:
                _sub(transpose, "octave-change", str(part_spec.transpose[2]))

    if measure_spec.repeat_forward:
        barline = _sub(measure, "barline", location="left")
        _sub(barline, "bar-style", "heavy-light")
        _sub(barline, "repeat", direction="forward")

    for onset, value in measure_spec.dynamics.items():
        direction = _sub(measure, "direction", placement="below")
        direction_type = _sub(direction, "direction-type")
        dynamics = _sub(direction_type, "dynamics")
        _sub(dynamics, value)
        if Fraction(onset):
            _sub(direction, "offset", str(int(Fraction(onset) * spec.divisions)))

    # Accidentals are engraved, not implied: a fixture that carries an altered <alter> with no
    # <accidental> is a file MuseScore would never write, and every consistency check in the
    # system would (correctly) light up on it. Track what is in force and print what is needed.
    key = KeySignature(fifths=fifths)
    in_force: dict[tuple[int, str, int], int] = {}

    previous_length = Fraction(0)
    for voice_index, (voice, pattern) in enumerate(sorted(measure_spec.voices.items())):
        staff = measure_spec.staff_of_voice.get(voice) if part_spec.staves > 1 else None
        note_specs = parse_pattern(pattern)
        if voice_index > 0 and previous_length > 0:
            # Every voice starts at the beginning of the measure, so rewind the cursor by
            # however much the previous voice consumed.
            backup = _sub(measure, "backup")
            _sub(backup, "duration", str(int(previous_length * spec.divisions)))
        for note_spec in note_specs:
            if note_spec.is_rest:
                _write_note(measure, note_spec, divisions=spec.divisions, voice=voice, staff=staff)
            else:
                for chord_index, pitch_token in enumerate(note_spec.pitches):
                    accidental = _accidental_for(pitch_token, key, in_force, staff or 1)
                    _write_note(
                        measure,
                        note_spec,
                        divisions=spec.divisions,
                        voice=voice,
                        staff=staff,
                        chord_index=chord_index,
                        pitch_token=pitch_token,
                        accidental=accidental,
                    )
        previous_length = sum((s.quarters for s in note_specs), Fraction(0))

    if measure_spec.repeat_backward:
        barline = _sub(measure, "barline", location="right")
        _sub(barline, "bar-style", "light-heavy")
        _sub(barline, "repeat", direction="backward")


def write_musicxml(spec: ScoreSpec, path: str) -> str:
    """Build and write a score, returning the path."""
    tree = build_musicxml(spec)
    tree.write(
        path,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
        doctype=(
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">'
        ),
    )
    return path
