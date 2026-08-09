"""Notation layout: score IR → positioned shapes.

Deliberately *not* a general-purpose engraver. This panel exists to answer one question — "does
the file say what the page says?" — and everything in the layout serves that:

* **Spacing is proportional to onset**, not optically balanced. A note at beat 3 sits three
  quarters of the way across its bar, which makes comparing against a scan straightforward.
* **Flags, not beams.** Beaming is a large amount of code that would make eighth-note runs
  prettier without making a single wrong note easier to spot.
* **Every shape carries the element handle it came from**, so highlighting a suggestion is a
  dictionary lookup rather than a re-layout.

The output is measured in staff spaces; backends scale once. No Qt, no XML — this module is
tested directly.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from fractions import Fraction

from ...models import (
    Chord,
    Clef,
    ClefSign,
    KeySignature,
    Measure,
    Note,
    Part,
    Rest,
    Score,
    Step,
    TimeSignature,
)
from ...models.score import FLAT_ORDER, SHARP_ORDER
from .glyphs import accidental_shapes, clef_shapes, flag_shapes, rest_shapes
from .primitives import (
    Anchor,
    Curve,
    Ellipse,
    Line,
    RenderedScore,
    RenderedSystem,
    RenderStyle,
    Shape,
    Text,
)

__all__ = ["LayoutEngine", "StaffPlacement", "layout_score"]

#: Diatonic index of each clef's reference pitch, for glyph anchoring.
_CLEF_ANCHOR_PITCH = {ClefSign.G: "G4", ClefSign.F: "F3", ClefSign.C: "C4"}

#: Conventional staff positions of key-signature accidentals in treble clef, in half-spaces
#: above the bottom line (0 = bottom line, 8 = top line).
_SHARP_POSITIONS: dict[Step, int] = {
    Step.F: 8,
    Step.C: 5,
    Step.G: 9,
    Step.D: 6,
    Step.A: 3,
    Step.E: 7,
    Step.B: 4,
}
_FLAT_POSITIONS: dict[Step, int] = {
    Step.B: 4,
    Step.E: 7,
    Step.A: 3,
    Step.D: 6,
    Step.G: 2,
    Step.C: 5,
    Step.F: 1,
}
#: Shift applied to those positions for clefs other than treble.
_CLEF_KEY_OFFSET: dict[tuple[ClefSign, int], int] = {
    (ClefSign.G, 2): 0,
    (ClefSign.F, 4): -2,
    (ClefSign.C, 3): -1,
    (ClefSign.C, 4): 1,
}

#: Written value → number of flags.
_FLAG_COUNT = {"eighth": 1, "16th": 2, "32nd": 3, "64th": 4, "128th": 5}
_HOLLOW_TYPES = {"whole", "breve", "half"}
_STEMLESS_TYPES = {"whole", "breve"}


@dataclass(frozen=True)
class StaffPlacement:
    """Where one staff of one part sits inside a system."""

    part: Part
    staff: int
    top: float
    label: str

    def line_y(self, line: int) -> float:
        """Pixel-space y of a staff line, 1 = bottom line."""
        return self.top + (5 - line) * 1.0

    @property
    def middle_y(self) -> float:
        return self.line_y(3)

    @property
    def bottom_y(self) -> float:
        return self.line_y(1)


class LayoutEngine:
    """Lays a score out into systems of staves."""

    def __init__(
        self,
        score: Score,
        style: RenderStyle | None = None,
        page_width: float = 120.0,
        measures_per_system: int | None = None,
    ) -> None:
        self.score = score
        self.style = style or RenderStyle()
        self.page_width = page_width
        self.measures_per_system = measures_per_system
        self.regions: dict[int, tuple[float, float, float, float]] = {}
        self.measure_spans: dict[int, tuple[float, float]] = {}

    # -- entry point ---------------------------------------------------------------

    def run(self, first_measure: int = 0, last_measure: int | None = None) -> RenderedScore:
        """Lay out a measure range. Rendering a whole orchestral score at once is neither useful
        nor fast, so the review UI asks for a window around the suggestion being examined."""
        total = self.score.measure_count
        if total == 0:
            return RenderedScore(width=self.page_width, height=10.0, style=self.style)
        last = total - 1 if last_measure is None else min(last_measure, total - 1)
        first = max(0, min(first_measure, last))

        groups = self._group_measures(first, last)
        systems: list[RenderedSystem] = []
        cursor_y = self.style.margin_y

        for index, (start, end) in enumerate(groups):
            system, height = self._layout_system(index, start, end, cursor_y)
            systems.append(system)
            cursor_y += height + self.style.system_gap

        return RenderedScore(
            width=self.page_width,
            height=cursor_y + self.style.margin_y,
            style=self.style,
            systems=systems,
            regions=self.regions,
            measure_spans=self.measure_spans,
        )

    # -- system planning -----------------------------------------------------------

    def _group_measures(self, first: int, last: int) -> list[tuple[int, int]]:
        """Break the requested range into systems that fit the page width."""
        if self.measures_per_system:
            return [
                (start, min(start + self.measures_per_system - 1, last))
                for start in range(first, last + 1, self.measures_per_system)
            ]

        available = self.page_width - self.style.margin_x * 2 - self.style.label_width
        groups: list[tuple[int, int]] = []
        start = first
        used = self._prefix_width(first)
        for index in range(first, last + 1):
            width = self._measure_width(index)
            if index > start and used + width > available:
                groups.append((start, index - 1))
                start = index
                used = self._prefix_width(index)
            used += width
        groups.append((start, last))
        return groups

    def _prefix_width(self, measure_index: int) -> float:
        """Width of the clef, key and time signature drawn at the start of a system."""
        width = self.style.clef_width
        key = self._key_at(measure_index)
        width += abs(key.fifths) * self.style.key_accidental_width + 0.6
        if self._time_at(measure_index) is not None:
            width += self.style.time_width
        return width

    def _measure_width(self, measure_index: int) -> float:
        events = 0
        for part in self.score.parts:
            measure = part.measure_by_index(measure_index)
            if measure is not None:
                events = max(events, len(measure.events))
        return max(self.style.min_measure_width, self.style.width_per_event * max(1, events) + 2.0)

    def _key_at(self, measure_index: int) -> KeySignature:
        for part in self.score.parts:
            measure = part.measure_by_index(measure_index)
            if measure is not None:
                return measure.attributes.key
        return KeySignature()

    def _time_at(self, measure_index: int) -> TimeSignature | None:
        for part in self.score.parts:
            measure = part.measure_by_index(measure_index)
            if measure is not None and measure.attributes.time is not None:
                if measure_index == 0:
                    return measure.attributes.time
                previous = part.measure_by_index(measure_index - 1)
                if previous is None or previous.attributes.time != measure.attributes.time:
                    return measure.attributes.time
        return None

    def _placements(self, top: float) -> list[StaffPlacement]:
        placements: list[StaffPlacement] = []
        cursor = top
        for part in self.score.parts:
            for staff in range(1, max(1, part.staves) + 1):
                label = part.display_name if staff == 1 else ""
                placements.append(StaffPlacement(part=part, staff=staff, top=cursor, label=label))
                cursor += self.style.staff_gap
        return placements

    # -- system layout -------------------------------------------------------------

    def _layout_system(
        self, index: int, first: int, last: int, top: float
    ) -> tuple[RenderedSystem, float]:
        placements = self._placements(top)
        shapes: list[Shape] = []
        left = self.style.margin_x + self.style.label_width
        prefix = self._prefix_width(first)
        content_left = left + prefix

        widths = [self._measure_width(measure) for measure in range(first, last + 1)]
        available = self.page_width - self.style.margin_x - content_left
        total = sum(widths) or 1.0
        scale = available / total if total > available else 1.0
        widths = [width * scale for width in widths]
        system_right = content_left + sum(widths)

        for placement in placements:
            shapes.extend(self._staff_lines(placement, left, system_right))
            if placement.label:
                shapes.append(
                    Text(
                        role="label",
                        x=self.style.margin_x,
                        y=placement.middle_y + 0.4,
                        text=placement.label,
                        size=self.style.label_font_size,
                    )
                )
            shapes.extend(self._prefix_shapes(placement, first, left))

        # Opening barline joining the staves of the system.
        if len(placements) > 1:
            shapes.append(
                Line(
                    role="barline",
                    x1=left,
                    y1=placements[0].line_y(5),
                    x2=left,
                    y2=placements[-1].line_y(1),
                    width=self.style.barline_width,
                )
            )

        cursor = content_left
        for offset, measure_index in enumerate(range(first, last + 1)):
            width = widths[offset]
            self.measure_spans[measure_index] = (cursor, cursor + width)
            for placement in placements:
                measure = placement.part.measure_by_index(measure_index)
                if measure is not None:
                    shapes.extend(self._measure_shapes(placement, measure, cursor, width))
            cursor += width
            shapes.extend(self._barline(placements, cursor))

        height = placements[-1].line_y(1) - top + 2.0 if placements else 6.0
        system = RenderedSystem(
            index=index,
            top=top,
            height=height,
            first_measure=first,
            last_measure=last,
            shapes=shapes,
        )
        return system, height

    def _staff_lines(self, placement: StaffPlacement, left: float, right: float) -> list[Shape]:
        return [
            Line(
                role="staff",
                x1=left,
                y1=placement.line_y(line),
                x2=right,
                y2=placement.line_y(line),
                width=self.style.staff_line_width,
            )
            for line in range(1, 6)
        ]

    def _barline(self, placements: list[StaffPlacement], x: float) -> list[Shape]:
        return [
            Line(
                role="barline",
                x1=x,
                y1=placement.line_y(5),
                x2=x,
                y2=placement.line_y(1),
                width=self.style.barline_width,
            )
            for placement in placements
        ]

    def _prefix_shapes(
        self, placement: StaffPlacement, measure_index: int, left: float
    ) -> list[Shape]:
        measure = placement.part.measure_by_index(measure_index)
        if measure is None:
            return []
        clef = measure.attributes.clef_for_staff(placement.staff)
        key = measure.attributes.key_for_staff(placement.staff)
        shapes: list[Shape] = []

        anchor_name = _CLEF_ANCHOR_PITCH.get(clef.sign)
        anchor_y = (
            self._y_for_diatonic(placement, clef, _diatonic_of(anchor_name))
            if anchor_name
            else placement.middle_y
        )
        shapes.extend(clef_shapes(clef.sign.value, left + 0.4, anchor_y, clef.ref))

        cursor = left + self.style.clef_width
        for step in self._key_steps(key):
            y = self._key_accidental_y(placement, clef, step, key.fifths > 0)
            shapes.extend(
                accidental_shapes("sharp" if key.fifths > 0 else "flat", cursor, y, key.ref)
            )
            cursor += self.style.key_accidental_width

        time = measure.attributes.time
        if time is not None and self._time_at(measure_index) is not None:
            shapes.append(
                Text(
                    ref=time.ref,
                    role="time",
                    x=cursor + 0.6,
                    y=placement.line_y(4) + 0.35,
                    text=time.beats,
                    size=self.style.font_size,
                    anchor=Anchor.MIDDLE,
                    bold=True,
                )
            )
            shapes.append(
                Text(
                    ref=time.ref,
                    role="time",
                    x=cursor + 0.6,
                    y=placement.line_y(2) + 0.35,
                    text=str(time.beat_type),
                    size=self.style.font_size,
                    anchor=Anchor.MIDDLE,
                    bold=True,
                )
            )
        return shapes

    @staticmethod
    def _key_steps(key: KeySignature) -> tuple[Step, ...]:
        if key.fifths > 0:
            return SHARP_ORDER[: key.fifths]
        if key.fifths < 0:
            return FLAT_ORDER[: -key.fifths]
        return ()

    def _key_accidental_y(
        self, placement: StaffPlacement, clef: Clef, step: Step, sharps: bool
    ) -> float:
        """Place a signature accidental where engravers actually put it.

        The positions are conventional rather than derived — G-sharp sits *above* the treble
        staff while every other sharp sits inside it, and no rule about octaves reproduces that.
        Other clefs are the treble positions shifted by a fixed amount, which is exactly how the
        convention is taught.
        """
        table = _SHARP_POSITIONS if sharps else _FLAT_POSITIONS
        position = table[step] + _CLEF_KEY_OFFSET.get((clef.sign, clef.line), 0)
        return placement.bottom_y - position / 2

    # -- measure contents ----------------------------------------------------------

    def _measure_shapes(
        self, placement: StaffPlacement, measure: Measure, x: float, width: float
    ) -> list[Shape]:
        shapes: list[Shape] = []
        length = measure.nominal_length or max(
            (measure.voice_length(voice) for voice in measure.voices), default=Fraction(4)
        )
        length = length or Fraction(4)
        usable = width - self.style.note_padding * 2

        starts: dict[int, tuple[float, float]] = {}
        for event in measure.events:
            if event.staff != placement.staff:
                continue
            position = x + self.style.note_padding + float(event.onset / length) * usable
            clef = self._clef_for(measure, placement.staff, event.onset)
            if isinstance(event, Rest):
                shapes.extend(
                    rest_shapes(event.duration.note_type, position, placement.middle_y, event.ref)
                )
                self._record(event.ref, position - 0.3, placement.line_y(5), 1.7, 4.0)
                continue

            notes = event.notes if isinstance(event, Chord) else (event,)
            drawn = self._chord_shapes(placement, clef, notes, event, position)
            shapes.extend(drawn)
            starts[event.ref] = (position, self._note_y(placement, clef, notes[0]))

        shapes.extend(self._tie_shapes(placement, measure, starts))
        return shapes

    @staticmethod
    def _clef_for(measure: Measure, staff: int, onset: Fraction) -> Clef:
        clef = measure.attributes.clef_for_staff(staff)
        for change in measure.attribute_changes:
            if change.onset <= onset and staff in change.clefs:
                clef = change.clefs[staff]
        return clef

    def _chord_shapes(
        self,
        placement: StaffPlacement,
        clef: Clef,
        notes: tuple[Note, ...],
        event: object,
        x: float,
    ) -> list[Shape]:
        shapes: list[Shape] = []
        style = self.style
        note_type = notes[0].duration.note_type or "quarter"
        hollow = note_type in _HOLLOW_TYPES
        positions = [self._note_y(placement, clef, note) for note in notes]
        middle = placement.middle_y
        upward = sum(positions) / len(positions) >= middle

        accidental_x = x - 1.0
        for note, y in zip(notes, positions, strict=True):
            shapes.extend(self._ledger_lines(placement, clef, note, x))
            shapes.append(
                Ellipse(
                    ref=note.ref,
                    role="notehead",
                    cx=x,
                    cy=y,
                    rx=style.notehead_width / 2,
                    ry=style.notehead_height / 2,
                    rotation=style.notehead_angle,
                    filled=not hollow,
                    width=0.14,
                )
            )
            self._record(
                note.ref,
                x - style.notehead_width / 2 - 0.2,
                y - style.notehead_height / 2 - 0.2,
                style.notehead_width + 0.4,
                style.notehead_height + 0.4,
            )
            if note.accidental is not None:
                shapes.extend(accidental_shapes(note.accidental.value, accidental_x, y, note.ref))
                accidental_x -= 1.1
            for index in range(notes[0].duration.dots):
                shapes.append(
                    Ellipse(
                        ref=note.ref,
                        role="dot",
                        cx=x + style.notehead_width / 2 + 0.45 + index * 0.4,
                        cy=y - 0.25,
                        rx=style.dot_radius,
                        ry=style.dot_radius,
                    )
                )

        if note_type not in _STEMLESS_TYPES:
            head_x = x + (style.notehead_width / 2 - 0.07) * (1 if upward else -1)
            # An up-stem springs from the *lowest* notehead of the chord (largest y, since y
            # grows downward) and runs past the highest; a down-stem does the reverse.
            anchor = max(positions) if upward else min(positions)
            far = (
                min(positions) - style.stem_length if upward else max(positions) + style.stem_length
            )
            shapes.append(
                Line(
                    ref=notes[0].ref,
                    role="stem",
                    x1=head_x,
                    y1=anchor,
                    x2=head_x,
                    y2=far,
                    width=style.stem_width,
                )
            )
            flags = _FLAG_COUNT.get(note_type, 0)
            if flags:
                shapes.extend(flag_shapes(flags, head_x, far, not upward, notes[0].ref))
        return shapes

    def _ledger_lines(
        self, placement: StaffPlacement, clef: Clef, note: Note, x: float
    ) -> list[Shape]:
        position = clef.staff_position(note.pitch)
        shapes: list[Shape] = []
        extent = self.style.notehead_width / 2 + self.style.ledger_extension
        line = 10
        while line <= position:
            shapes.append(
                Line(
                    ref=note.ref,
                    role="ledger",
                    x1=x - extent,
                    y1=placement.bottom_y - line / 2,
                    x2=x + extent,
                    y2=placement.bottom_y - line / 2,
                    width=self.style.staff_line_width * 1.4,
                )
            )
            line += 2
        line = -2
        while line >= position:
            shapes.append(
                Line(
                    ref=note.ref,
                    role="ledger",
                    x1=x - extent,
                    y1=placement.bottom_y - line / 2,
                    x2=x + extent,
                    y2=placement.bottom_y - line / 2,
                    width=self.style.staff_line_width * 1.4,
                )
            )
            line -= 2
        return shapes

    def _tie_shapes(
        self,
        placement: StaffPlacement,
        measure: Measure,
        starts: dict[int, tuple[float, float]],
    ) -> list[Shape]:
        """Ties and slurs within this measure, drawn between the events they connect."""
        shapes: list[Shape] = []
        ordered = [
            event
            for event in measure.events
            if event.staff == placement.staff and event.ref in starts
        ]
        for first, second in itertools.pairwise(ordered):
            first_notes = first.notes if isinstance(first, Chord) else (first,)
            second_notes = second.notes if isinstance(second, Chord) else (second,)
            joined = any(
                isinstance(note, Note) and note.tie.starts for note in first_notes
            ) and any(isinstance(note, Note) and note.tie.stops for note in second_notes)
            slurred = any(
                isinstance(note, Note) and any(s.kind == "slur" for s in note.spanners)
                for note in first_notes
            )
            if not joined and not slurred:
                continue
            x1, y1 = starts[first.ref]
            x2, y2 = starts[second.ref]
            shapes.append(
                Curve(
                    ref=first.ref,
                    role="tie" if joined else "slur",
                    x1=x1 + 0.4,
                    y1=y1 + 0.6,
                    x2=x2 - 0.4,
                    y2=y2 + 0.6,
                    bulge=0.9,
                    width=0.12,
                )
            )
        return shapes

    # -- geometry ------------------------------------------------------------------

    def _note_y(self, placement: StaffPlacement, clef: Clef, note: Note) -> float:
        return placement.bottom_y - clef.staff_position(note.pitch) / 2

    def _y_for_diatonic(self, placement: StaffPlacement, clef: Clef, diatonic: int) -> float:
        return placement.bottom_y - (diatonic - clef.bottom_line_diatonic) / 2

    def _record(self, ref: int, x: float, y: float, width: float, height: float) -> None:
        if ref < 0:
            return
        existing = self.regions.get(ref)
        if existing is None:
            self.regions[ref] = (x, y, width, height)
            return
        left = min(existing[0], x)
        top = min(existing[1], y)
        right = max(existing[0] + existing[2], x + width)
        bottom = max(existing[1] + existing[3], y + height)
        self.regions[ref] = (left, top, right - left, bottom - top)


def _diatonic_of(name: str) -> int:
    from ...models import Pitch

    return Pitch.from_name(name).diatonic


def layout_score(
    score: Score,
    style: RenderStyle | None = None,
    page_width: float = 120.0,
    first_measure: int = 0,
    last_measure: int | None = None,
) -> RenderedScore:
    """Convenience wrapper around :class:`LayoutEngine`."""
    engine = LayoutEngine(score, style=style, page_width=page_width)
    return engine.run(first_measure=first_measure, last_measure=last_measure)
