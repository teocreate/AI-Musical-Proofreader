"""MusicXML → internal representation.

The parser is a single forward pass per part, maintaining a *cursor* (position inside the current
measure, in quarter notes) and an *attribute state* (divisions, key, time, clefs, transposition)
that survives across measures. That mirrors how MusicXML actually works: attributes are sticky,
``<backup>``/``<forward>`` move the cursor so multiple voices can be written sequentially, and a
``<chord/>`` flag means "this note starts where the previous one did".

Design notes worth knowing before changing anything here:

* **Every parsed element is registered** with the :class:`SourceDocument` and its handle stored on
  the IR object. Skipping registration for an element means no correction can ever target it.
* **Recovery is per measure.** A measure that raises is recorded as a
  :class:`~ai_proofreader.models.score.ParseIssue` and skipped; the rest of the part is still
  analysed. Scores that come out of OMR are exactly the scores most likely to be malformed, so
  aborting the whole run on one bad bar would fail the users who need this most.
* **No regexes, no XPath in the hot loop.** ``iterchildren`` and dictionary dispatch only; this
  pass has to stay inside the 6-second budget for a 100-page orchestral score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from fractions import Fraction

from lxml import etree

from ..models import (
    AttributeChange,
    Barline,
    BarlineLocation,
    Chord,
    Clef,
    ClefSign,
    Direction,
    DirectionKind,
    Duration,
    Event,
    KeySignature,
    Lyric,
    Measure,
    MeasureAttributes,
    Mode,
    Note,
    ParseIssue,
    Part,
    PartGroup,
    Pitch,
    RepeatDirection,
    Rest,
    Score,
    ScoreMetadata,
    Spanner,
    SpannerRole,
    StemDirection,
    Step,
    TieState,
    TimeModification,
    TimeSignature,
    Transpose,
)
from ..models.pitch import Accidental
from .document import SourceDocument, localname

__all__ = ["MusicXmlParser", "parse_score"]

logger = logging.getLogger(__name__)

_DIRECTION_TYPE_MAP: dict[str, DirectionKind] = {
    "dynamics": DirectionKind.DYNAMICS,
    "words": DirectionKind.WORDS,
    "metronome": DirectionKind.METRONOME,
    "wedge": DirectionKind.WEDGE,
    "octave-shift": DirectionKind.OCTAVE_SHIFT,
    "pedal": DirectionKind.PEDAL,
    "rehearsal": DirectionKind.REHEARSAL,
    "segno": DirectionKind.SEGNO,
    "coda": DirectionKind.CODA,
    "dashes": DirectionKind.DASHES,
    "bracket": DirectionKind.BRACKET,
}

#: ``<dynamics>`` wraps the marking as an empty child element: ``<dynamics><mf/></dynamics>``.
_DYNAMIC_WORDS = frozenset(
    {
        "p", "pp", "ppp", "pppp", "ppppp", "pppppp",
        "f", "ff", "fff", "ffff", "fffff", "ffffff",
        "mp", "mf", "sf", "sfp", "sfpp", "fp", "rf", "rfz", "sfz", "sffz", "fz", "n", "pf", "sfzp",
    }
)  # fmt: skip


def _text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return (element.text or "").strip()


def _child(parent: etree._Element, name: str) -> etree._Element | None:
    for child in parent.iterchildren():
        if localname(child.tag) == name:
            return child
    return None


def _child_text(parent: etree._Element, name: str) -> str:
    return _text(_child(parent, name))


def _child_int(parent: etree._Element, name: str, default: int | None = None) -> int | None:
    text = _child_text(parent, name)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _attr_int(element: etree._Element, name: str, default: int | None = None) -> int | None:
    value = element.get(name)
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _is_yes(value: str | None) -> bool:
    return (value or "").strip().lower() == "yes"


@dataclass
class _PartState:
    """Attribute state that persists across measures within a part."""

    divisions: int = 1
    key: KeySignature = field(default_factory=KeySignature)
    keys_by_staff: dict[int, KeySignature] = field(default_factory=dict)
    time: TimeSignature | None = None
    clefs: dict[int, Clef] = field(default_factory=dict)
    staves: int = 1
    transpose: Transpose = field(default_factory=Transpose)

    def snapshot(self) -> MeasureAttributes:
        return MeasureAttributes(
            divisions=self.divisions,
            key=self.key,
            keys_by_staff=dict(self.keys_by_staff),
            time=self.time,
            clefs=dict(self.clefs),
            staves=self.staves,
        )


class MusicXmlParser:
    """Turns a :class:`SourceDocument` into a :class:`~ai_proofreader.models.score.Score`."""

    def __init__(self, document: SourceDocument) -> None:
        self.document = document
        self.issues: list[ParseIssue] = []

    # -- entry point ---------------------------------------------------------------

    def parse(self) -> Score:
        self.document.reset_handles()
        self.issues = []
        root = self.document.tree.getroot()

        metadata = self._parse_metadata(root)
        part_info, groups = self._parse_part_list(root)

        parts: list[Part] = []
        for part_element in root.iterchildren():
            if localname(part_element.tag) != "part":
                continue
            part_id = part_element.get("id") or f"P{len(parts) + 1}"
            info = part_info.get(part_id, {})
            try:
                parts.append(self._parse_part(part_element, part_id, info))
            except Exception as exc:  # pragma: no cover - defensive, per-part isolation
                logger.exception("part %s failed to parse", part_id)
                self.issues.append(
                    ParseIssue(
                        severity="error",
                        message=f"Part {part_id} could not be parsed and was skipped",
                        part_id=part_id,
                        detail=str(exc),
                    )
                )

        return Score(
            metadata=metadata,
            parts=tuple(parts),
            part_groups=tuple(groups),
            issues=tuple(self.issues),
        )

    # -- header --------------------------------------------------------------------

    def _parse_metadata(self, root: etree._Element) -> ScoreMetadata:
        title = ""
        subtitle = ""
        composer = ""
        lyricist = ""
        rights = ""
        software = ""
        encoding_date = ""

        work = _child(root, "work")
        if work is not None:
            title = _child_text(work, "work-title")
        movement_title = _child_text(root, "movement-title")
        if movement_title and not title:
            title = movement_title
        elif movement_title:
            subtitle = movement_title

        identification = _child(root, "identification")
        if identification is not None:
            for child in identification.iterchildren():
                tag = localname(child.tag)
                if tag == "creator":
                    role = (child.get("type") or "").lower()
                    if role == "composer":
                        composer = _text(child)
                    elif role in {"lyricist", "poet"}:
                        lyricist = _text(child)
                elif tag == "rights":
                    rights = _text(child)
                elif tag == "encoding":
                    software = _child_text(child, "software")
                    encoding_date = _child_text(child, "encoding-date")

        if not title:
            # MuseScore often puts the title only in a <credit-words> block.
            for credit in root.iterchildren():
                if localname(credit.tag) == "credit":
                    words = _child(credit, "credit-words")
                    if words is not None and _text(words):
                        title = _text(words)
                        break

        return ScoreMetadata(
            title=title,
            subtitle=subtitle,
            composer=composer,
            lyricist=lyricist,
            copyright=rights,
            software=software,
            encoding_date=encoding_date,
            source_path=str(self.document.path),
            source_format=self.document.source_format,
        )

    def _parse_part_list(
        self, root: etree._Element
    ) -> tuple[dict[str, dict[str, object]], list[PartGroup]]:
        info: dict[str, dict[str, object]] = {}
        groups: list[PartGroup] = []
        open_groups: dict[str, dict[str, object]] = {}

        part_list = _child(root, "part-list")
        if part_list is None:
            return info, groups

        for element in part_list.iterchildren():
            tag = localname(element.tag)
            if tag == "score-part":
                part_id = element.get("id") or ""
                midi_program = None
                midi_channel = None
                instrument = _child(element, "midi-instrument")
                if instrument is not None:
                    midi_program = _child_int(instrument, "midi-program")
                    midi_channel = _child_int(instrument, "midi-channel")
                info[part_id] = {
                    "name": _child_text(element, "part-name"),
                    "abbreviation": _child_text(element, "part-abbreviation"),
                    "midi_program": midi_program,
                    "midi_channel": midi_channel,
                    "ref": self.document.register(element),
                }
                for open_group in open_groups.values():
                    members: list[str] = open_group["part_ids"]  # type: ignore[assignment]
                    members.append(part_id)
            elif tag == "part-group":
                number = element.get("number") or "1"
                if (element.get("type") or "start") == "start":
                    open_groups[number] = {
                        "name": _child_text(element, "group-name") or None,
                        "symbol": _child_text(element, "group-symbol") or None,
                        "part_ids": [],
                    }
                else:
                    finished = open_groups.pop(number, None)
                    if finished is not None:
                        groups.append(
                            PartGroup(
                                number=number,
                                name=finished["name"],  # type: ignore[arg-type]
                                symbol=finished["symbol"],  # type: ignore[arg-type]
                                part_ids=tuple(finished["part_ids"]),  # type: ignore[arg-type]
                            )
                        )
        return info, groups

    # -- parts and measures --------------------------------------------------------

    def _parse_part(self, element: etree._Element, part_id: str, info: dict[str, object]) -> Part:
        state = _PartState()
        measures: list[Measure] = []
        index = 0

        for measure_element in element.iterchildren():
            if localname(measure_element.tag) != "measure":
                continue
            try:
                measure = self._parse_measure(measure_element, state, index, part_id)
            except Exception as exc:
                logger.warning("measure %s of part %s failed: %s", index + 1, part_id, exc)
                self.issues.append(
                    ParseIssue(
                        severity="error",
                        message=f"Measure {index + 1} could not be parsed and was skipped",
                        part_id=part_id,
                        measure_index=index,
                        detail=str(exc),
                    )
                )
                index += 1
                continue
            measures.append(measure)
            index += 1

        return Part(
            ref=int(info.get("ref", -1) or -1),  # type: ignore[arg-type]
            id=part_id,
            name=str(info.get("name") or ""),
            abbreviation=str(info.get("abbreviation") or ""),
            midi_program=info.get("midi_program"),  # type: ignore[arg-type]
            midi_channel=info.get("midi_channel"),  # type: ignore[arg-type]
            staves=state.staves,
            transpose=state.transpose,
            measures=tuple(measures),
        )

    def _parse_measure(
        self, element: etree._Element, state: _PartState, index: int, part_id: str
    ) -> Measure:
        ref = self.document.register(element)
        cursor = Fraction(0)
        last_onset = Fraction(0)

        events: list[Event] = []
        directions: list[Direction] = []
        barlines: list[Barline] = []
        changes: list[AttributeChange] = []
        chord_buffer: list[Note] = []
        start_attributes: MeasureAttributes | None = None
        seen_content = False

        def flush_chord() -> None:
            """Collapse a run of ``<chord/>``-linked notes into one :class:`Chord` event."""
            nonlocal chord_buffer
            if not chord_buffer:
                return
            if len(chord_buffer) == 1:
                events.append(chord_buffer[0])
            else:
                first = chord_buffer[0]
                events.append(
                    Chord(
                        ref=first.ref,
                        onset=first.onset,
                        duration=first.duration,
                        voice=first.voice,
                        staff=first.staff,
                        notes=tuple(chord_buffer),
                    )
                )
            chord_buffer = []

        for child in element.iterchildren():
            tag = localname(child.tag)

            if tag == "attributes":
                if not seen_content and cursor == 0:
                    self._apply_attributes(child, state)
                    start_attributes = state.snapshot()
                else:
                    flush_chord()
                    changes.append(self._apply_attributes(child, state, onset=cursor))
                continue

            if tag == "note":
                seen_content = True
                if start_attributes is None:
                    start_attributes = state.snapshot()
                is_chord_member = _child(child, "chord") is not None
                if not is_chord_member:
                    flush_chord()
                note_or_rest, advance = self._parse_note(
                    child, state, cursor if not is_chord_member else last_onset
                )
                if isinstance(note_or_rest, Rest):
                    events.append(note_or_rest)
                else:
                    chord_buffer.append(note_or_rest)
                if not is_chord_member:
                    last_onset = cursor
                    cursor += advance
                continue

            if tag == "backup":
                flush_chord()
                seen_content = True
                ticks = _child_int(child, "duration", 0) or 0
                cursor = self._quantize(max(Fraction(0), cursor - Fraction(ticks, state.divisions)))
                continue

            if tag == "forward":
                flush_chord()
                seen_content = True
                ticks = _child_int(child, "duration", 0) or 0
                cursor += Fraction(ticks, state.divisions)
                continue

            if tag == "direction":
                if start_attributes is None:
                    start_attributes = state.snapshot()
                directions.extend(self._parse_direction(child, state, cursor))
                continue

            if tag == "barline":
                barlines.append(self._parse_barline(child))
                continue

        flush_chord()
        if start_attributes is None:
            start_attributes = state.snapshot()

        events.sort(key=lambda event: event.onset)

        return Measure(
            ref=ref,
            index=index,
            number=element.get("number") or str(index + 1),
            implicit=_is_yes(element.get("implicit")),
            attributes=start_attributes,
            attribute_changes=tuple(changes),
            events=tuple(events),
            directions=tuple(directions),
            barlines=tuple(barlines),
            width=float(element.get("width")) if element.get("width") else None,
        )

    @staticmethod
    def _quantize(value: Fraction) -> Fraction:
        """Guard against pathological denominators from broken division counts."""
        return value if value.denominator <= 10080 else value.limit_denominator(10080)

    # -- attributes ----------------------------------------------------------------

    def _apply_attributes(
        self, element: etree._Element, state: _PartState, onset: Fraction | None = None
    ) -> AttributeChange:
        """Fold an ``<attributes>`` element into ``state`` and describe what changed."""
        changed_key: KeySignature | None = None
        changed_time: TimeSignature | None = None
        changed_clefs: dict[int, Clef] = {}
        changed_divisions: int | None = None

        for child in element.iterchildren():
            tag = localname(child.tag)
            if tag == "divisions":
                value = _child_int(element, "divisions")
                if value and value > 0:
                    state.divisions = value
                    changed_divisions = value
            elif tag == "staves":
                state.staves = max(state.staves, int(_text(child) or 1))
            elif tag == "key":
                key = self._parse_key(child)
                staff = _attr_int(child, "number")
                if staff is None:
                    state.key = key
                    state.keys_by_staff.clear()
                else:
                    state.keys_by_staff[staff] = key
                changed_key = key
            elif tag == "time":
                time = self._parse_time(child)
                state.time = time
                changed_time = time
            elif tag == "clef":
                clef = self._parse_clef(child)
                state.clefs[clef.staff] = clef
                changed_clefs[clef.staff] = clef
            elif tag == "transpose":
                state.transpose = Transpose(
                    diatonic=_child_int(child, "diatonic", 0) or 0,
                    chromatic=_child_int(child, "chromatic", 0) or 0,
                    octave_change=_child_int(child, "octave-change", 0) or 0,
                    double=_child(child, "double") is not None,
                )

        return AttributeChange(
            ref=self.document.register(element),
            onset=onset if onset is not None else Fraction(0),
            key=changed_key,
            time=changed_time,
            clefs=changed_clefs,
            divisions=changed_divisions,
        )

    def _parse_key(self, element: etree._Element) -> KeySignature:
        fifths = _child_int(element, "fifths", 0) or 0
        mode_text = _child_text(element, "mode").lower()
        try:
            mode = Mode(mode_text) if mode_text else Mode.MAJOR
        except ValueError:
            mode = Mode.NONE
        return KeySignature(
            ref=self.document.register(element),
            fifths=max(-7, min(7, fifths)),
            mode=mode,
            staff=_attr_int(element, "number"),
        )

    def _parse_time(self, element: etree._Element) -> TimeSignature:
        senza = _child(element, "senza-misura") is not None
        beats_parts: list[str] = []
        beat_type = 4
        for child in element.iterchildren():
            tag = localname(child.tag)
            if tag == "beats":
                beats_parts.append(_text(child))
            elif tag == "beat-type":
                try:
                    beat_type = int(_text(child))
                except ValueError:
                    beat_type = 4
        return TimeSignature(
            ref=self.document.register(element),
            beats="+".join(p for p in beats_parts if p) or "4",
            beat_type=beat_type,
            symbol=element.get("symbol"),
            senza_misura=senza,
        )

    def _parse_clef(self, element: etree._Element) -> Clef:
        sign_text = _child_text(element, "sign") or "G"
        try:
            sign = ClefSign(sign_text)
        except ValueError:
            sign = ClefSign.G
        default_line = {"G": 2, "F": 4, "C": 3}.get(sign_text, 2)
        return Clef(
            ref=self.document.register(element),
            sign=sign,
            line=_child_int(element, "line", default_line) or default_line,
            octave_change=_child_int(element, "clef-octave-change", 0) or 0,
            staff=_attr_int(element, "number", 1) or 1,
        )

    # -- notes ---------------------------------------------------------------------

    def _parse_note(
        self, element: etree._Element, state: _PartState, onset: Fraction
    ) -> tuple[Note | Rest, Fraction]:
        """Parse one ``<note>``. Returns the event and how far the cursor should advance."""
        ref = self.document.register(element)
        is_grace = _child(element, "grace") is not None
        rest_element = _child(element, "rest")

        ticks = _child_int(element, "duration", 0) or 0
        quarter_length = Fraction(0) if is_grace else Fraction(ticks, max(1, state.divisions))
        quarter_length = self._quantize(quarter_length)

        note_type = _child_text(element, "type") or None
        dots = sum(1 for c in element.iterchildren() if localname(c.tag) == "dot")

        time_mod_element = _child(element, "time-modification")
        time_modification = None
        if time_mod_element is not None:
            actual = _child_int(time_mod_element, "actual-notes", 1) or 1
            normal = _child_int(time_mod_element, "normal-notes", 1) or 1
            time_modification = TimeModification(
                actual_notes=max(1, actual),
                normal_notes=max(1, normal),
                normal_type=_child_text(time_mod_element, "normal-type") or None,
            )

        duration = Duration(
            quarter_length=quarter_length,
            note_type=note_type,
            dots=dots,
            time_modification=time_modification,
            ticks=max(0, ticks),
        )

        voice = _child_text(element, "voice") or "1"
        staff = _child_int(element, "staff", 1) or 1
        advance = quarter_length

        notations = _child(element, "notations")
        spanners, articulations, ornaments, technical, fermata, tied = self._parse_notations(
            notations
        )

        if rest_element is not None:
            return (
                Rest(
                    ref=ref,
                    onset=onset,
                    duration=duration,
                    voice=voice,
                    staff=staff,
                    is_measure_rest=_is_yes(rest_element.get("measure")),
                    display_step=_child_text(rest_element, "display-step") or None,
                    display_octave=_child_int(rest_element, "display-octave"),
                    fermata=fermata,
                    spanners=spanners,
                ),
                advance,
            )

        pitch_element = _child(element, "pitch")
        if pitch_element is None:
            pitch_element = _child(element, "unpitched")
        pitch = self._parse_pitch(pitch_element)

        accidental_element = _child(element, "accidental")
        accidental: Accidental | None = None
        if accidental_element is not None:
            try:
                accidental = Accidental(_text(accidental_element))
            except ValueError:
                accidental = Accidental.OTHER

        tie_state = self._parse_ties(element, tied)
        stem_text = _child_text(element, "stem")
        stem: StemDirection | None = None
        if stem_text:
            try:
                stem = StemDirection(stem_text)
            except ValueError:
                stem = None

        beams = tuple(
            _text(child) for child in element.iterchildren() if localname(child.tag) == "beam"
        )
        lyrics = tuple(
            self._parse_lyric(child)
            for child in element.iterchildren()
            if localname(child.tag) == "lyric"
        )

        note = Note(
            ref=ref,
            onset=onset,
            duration=duration,
            voice=voice,
            staff=staff,
            pitch=pitch,
            accidental=accidental,
            accidental_cautionary=_is_yes(
                accidental_element.get("cautionary") if accidental_element is not None else None
            ),
            accidental_editorial=_is_yes(
                accidental_element.get("editorial") if accidental_element is not None else None
            ),
            is_grace=is_grace,
            grace_slash=_is_yes(
                _child(element, "grace").get("slash") if is_grace else None  # type: ignore[union-attr]
            ),
            is_cue=_child(element, "cue") is not None,
            tie=tie_state,
            spanners=spanners,
            articulations=articulations,
            ornaments=ornaments,
            technical=technical,
            fermata=fermata,
            stem=stem,
            beams=beams,
            notehead=_child_text(element, "notehead") or None,
            lyrics=lyrics,
        )
        return note, advance

    @staticmethod
    def _parse_pitch(element: etree._Element | None) -> Pitch:
        if element is None:
            return Pitch(step=Step.C, alter=0, octave=4)
        step_text = _child_text(element, "step") or _child_text(element, "display-step") or "C"
        octave_text = (
            _child_text(element, "octave") or _child_text(element, "display-octave") or "4"
        )
        alter_text = _child_text(element, "alter")
        try:
            step = Step(step_text.upper())
        except ValueError:
            step = Step.C
        try:
            octave = int(octave_text)
        except ValueError:
            octave = 4
        alter = 0
        if alter_text:
            try:
                # Microtonal alterations exist in the wild; we round to the nearest semitone
                # rather than refusing the file.
                alter = round(float(alter_text))
            except ValueError:
                alter = 0
        return Pitch(step=step, alter=max(-4, min(4, alter)), octave=max(-2, min(10, octave)))

    @staticmethod
    def _parse_ties(element: etree._Element, tied: tuple[bool, bool]) -> TieState:
        sounds_start = False
        sounds_stop = False
        for child in element.iterchildren():
            if localname(child.tag) != "tie":
                continue
            tie_type = (child.get("type") or "").lower()
            if tie_type == "start":
                sounds_start = True
            elif tie_type == "stop":
                sounds_stop = True
        return TieState(
            sounds_start=sounds_start,
            sounds_stop=sounds_stop,
            prints_start=tied[0],
            prints_stop=tied[1],
        )

    def _parse_notations(self, notations: etree._Element | None) -> tuple[
        tuple[Spanner, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        bool,
        tuple[bool, bool],
    ]:
        spanners: list[Spanner] = []
        articulations: list[str] = []
        ornaments: list[str] = []
        technical: list[str] = []
        fermata = False
        tied_start = False
        tied_stop = False

        if notations is None:
            return ((), (), (), (), False, (False, False))

        for child in notations.iterchildren():
            tag = localname(child.tag)
            if tag == "tied":
                role = (child.get("type") or "").lower()
                if role == "start":
                    tied_start = True
                elif role == "stop":
                    tied_stop = True
            elif tag == "slur":
                role_text = (child.get("type") or "start").lower()
                try:
                    role = SpannerRole(role_text)
                except ValueError:
                    continue
                spanners.append(
                    Spanner(
                        kind="slur",
                        role=role,
                        number=_attr_int(child, "number", 1) or 1,
                        placement=child.get("placement"),
                    )
                )
            elif tag == "tuplet":
                role_text = (child.get("type") or "start").lower()
                try:
                    role = SpannerRole(role_text)
                except ValueError:
                    continue
                spanners.append(
                    Spanner(
                        kind="tuplet",
                        role=role,
                        number=_attr_int(child, "number", 1) or 1,
                        placement=child.get("placement"),
                    )
                )
            elif tag == "glissando" or tag == "slide":
                role_text = (child.get("type") or "start").lower()
                if role_text in {"start", "stop"}:
                    spanners.append(
                        Spanner(
                            kind=tag,
                            role=SpannerRole(role_text),
                            number=_attr_int(child, "number", 1) or 1,
                        )
                    )
            elif tag == "articulations":
                articulations.extend(localname(c.tag) for c in child.iterchildren())
            elif tag == "ornaments":
                ornaments.extend(
                    localname(c.tag)
                    for c in child.iterchildren()
                    if localname(c.tag) != "accidental-mark"
                )
            elif tag == "technical":
                technical.extend(localname(c.tag) for c in child.iterchildren())
            elif tag == "fermata":
                fermata = True

        return (
            tuple(spanners),
            tuple(articulations),
            tuple(ornaments),
            tuple(technical),
            fermata,
            (tied_start, tied_stop),
        )

    @staticmethod
    def _parse_lyric(element: etree._Element) -> Lyric:
        return Lyric(
            number=_attr_int(element, "number", 1) or 1,
            text=_child_text(element, "text"),
            syllabic=_child_text(element, "syllabic") or None,
        )

    # -- directions and barlines ---------------------------------------------------

    def _parse_direction(
        self, element: etree._Element, state: _PartState, cursor: Fraction
    ) -> list[Direction]:
        ref = self.document.register(element)
        offset_ticks = _child_int(element, "offset", 0) or 0
        onset = cursor + Fraction(offset_ticks, max(1, state.divisions))
        onset = max(Fraction(0), self._quantize(onset))
        staff = _child_int(element, "staff", 1) or 1
        voice = _child_text(element, "voice") or None
        placement = element.get("placement")

        sound = _child(element, "sound")
        sound_tempo: float | None = None
        if sound is not None and sound.get("tempo"):
            try:
                sound_tempo = float(sound.get("tempo") or "")
            except ValueError:
                sound_tempo = None

        results: list[Direction] = []
        for direction_type in element.iterchildren():
            if localname(direction_type.tag) != "direction-type":
                continue
            for child in direction_type.iterchildren():
                tag = localname(child.tag)
                kind = _DIRECTION_TYPE_MAP.get(tag, DirectionKind.OTHER)
                value: str | None = None
                text: str | None = None
                spanner: Spanner | None = None
                tempo_bpm: float | None = sound_tempo
                beat_unit: str | None = None

                if tag == "dynamics":
                    marks = [localname(c.tag) for c in child.iterchildren()]
                    other = _child_text(child, "other-dynamics")
                    value = (
                        next((m for m in marks if m in _DYNAMIC_WORDS), None)
                        or other
                        or (marks[0] if marks else None)
                    )
                elif tag == "words":
                    text = _text(child)
                    value = text
                elif tag == "metronome":
                    beat_unit = _child_text(child, "beat-unit") or None
                    per_minute = _child_text(child, "per-minute")
                    if per_minute:
                        try:
                            tempo_bpm = float(per_minute.split("-")[0].strip())
                        except ValueError:
                            tempo_bpm = sound_tempo
                    value = f"{beat_unit or 'quarter'} = {per_minute}" if per_minute else None
                elif tag in {"wedge", "octave-shift", "pedal", "dashes", "bracket"}:
                    role_text = (child.get("type") or "start").lower()
                    role = {
                        "crescendo": SpannerRole.START,
                        "diminuendo": SpannerRole.START,
                        "start": SpannerRole.START,
                        "up": SpannerRole.START,
                        "down": SpannerRole.START,
                        "stop": SpannerRole.STOP,
                        "continue": SpannerRole.CONTINUE,
                    }.get(role_text, SpannerRole.START)
                    value = role_text
                    spanner = Spanner(
                        kind=tag,
                        role=role,
                        number=_attr_int(child, "number", 1) or 1,
                        placement=placement,
                        value=role_text,
                    )
                    if tag == "octave-shift":
                        spanner = spanner.model_copy(
                            update={"value": f"{role_text}:{child.get('size') or '8'}"}
                        )
                elif tag in {"rehearsal", "segno", "coda"}:
                    text = _text(child) or tag
                    value = text

                results.append(
                    Direction(
                        ref=ref,
                        kind=kind,
                        onset=onset,
                        staff=staff,
                        voice=voice,
                        value=value,
                        text=text,
                        placement=placement,
                        tempo_bpm=tempo_bpm,
                        tempo_beat_unit=beat_unit,
                        spanner=spanner,
                    )
                )

        if not results and sound_tempo is not None:
            results.append(
                Direction(
                    ref=ref,
                    kind=DirectionKind.METRONOME,
                    onset=onset,
                    staff=staff,
                    tempo_bpm=sound_tempo,
                )
            )
        return results

    def _parse_barline(self, element: etree._Element) -> Barline:
        location_text = (element.get("location") or "right").lower()
        try:
            location = BarlineLocation(location_text)
        except ValueError:
            location = BarlineLocation.RIGHT

        repeat_element = _child(element, "repeat")
        repeat: RepeatDirection | None = None
        repeat_times: int | None = None
        if repeat_element is not None:
            try:
                repeat = RepeatDirection((repeat_element.get("direction") or "backward").lower())
            except ValueError:
                repeat = None
            repeat_times = _attr_int(repeat_element, "times")

        ending_element = _child(element, "ending")
        ending_numbers: tuple[int, ...] = ()
        ending_type: str | None = None
        if ending_element is not None:
            raw = ending_element.get("number") or ""
            numbers: list[int] = []
            for token in raw.replace(" ", "").split(","):
                if token.isdigit():
                    numbers.append(int(token))
            ending_numbers = tuple(numbers)
            ending_type = ending_element.get("type")

        return Barline(
            ref=self.document.register(element),
            location=location,
            bar_style=_child_text(element, "bar-style") or None,
            repeat=repeat,
            repeat_times=repeat_times,
            ending_numbers=ending_numbers,
            ending_type=ending_type,
            fermata=_child(element, "fermata") is not None,
        )


def parse_score(document: SourceDocument) -> Score:
    """Convenience wrapper: parse ``document`` into a :class:`Score`."""
    return MusicXmlParser(document).parse()
