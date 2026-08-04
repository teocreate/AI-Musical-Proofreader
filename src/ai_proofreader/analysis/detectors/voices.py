"""Voice-assignment detectors.

When OMR splits a chord badly or assigns a note to the wrong voice, the result is usually
*impossible* rather than merely unusual: two notes in the same voice sounding at once without
being a chord, or a voice that briefly jumps above the one it has been sitting under for the whole
piece. Both are structural, both are cheap to find, and neither needs the scan.

What this module deliberately does *not* do is flag stem directions. MuseScore recomputes stems on
layout, so a "wrong" stem tells us nothing about the page (see ``docs/ARCHITECTURE.md`` §2.5). It
appears here only as a corroborating detail inside a finding that already stands on its own.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Iterable
from fractions import Fraction

from ...models import (
    Channel,
    Chord,
    Event,
    Measure,
    Note,
    Part,
    SetVoiceOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ..base import Detector, make_suggestion
from ..context import AnalysisContext

__all__ = ["VoiceCrossingDetector", "VoiceOverlapDetector"]


class VoiceOverlapDetector(Detector):
    """Two events in one voice sounding at the same time.

    A voice is monophonic by definition — simultaneous notes belong in a chord. When they are not
    in one, either the chord was split across separate ``<note>`` elements without the ``<chord/>``
    flag, or a duration is too long and runs over the next note.

    The second case needs a word of explanation, because within one voice MusicXML derives onsets
    from durations and an overlap looks impossible. It arises when the file contains a
    ``<backup>`` that rewinds into music already written for the same voice — the usual result of
    an engine mis-segmenting a system, and of hand-edited or converted files. Rare, cheap to
    check, and unambiguous when it happens.
    """

    name = "voice_overlap"
    description = "Notes in a single voice that sound simultaneously"
    kinds = (SuggestionKind.VOICE, SuggestionKind.DURATION)
    channel = Channel.MUSICAL
    defaults = {"chord_split_score": 0.8, "duration_overrun_score": 0.72}

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part, measure in context.score.iter_measures():
            by_voice: dict[tuple[int, str], list[Event]] = defaultdict(list)
            for event in measure.events:
                if event.duration.is_measured:
                    by_voice[(event.staff, event.voice)].append(event)
            for (staff, voice), events in by_voice.items():
                events.sort(key=lambda event: (event.onset, event.end))
                yield from self._check_sequence(part, measure, staff, voice, events)

    def _check_sequence(
        self, part: Part, measure: Measure, staff: int, voice: str, events: list[Event]
    ) -> Iterable[Suggestion]:
        for first, second in itertools.pairwise(events):
            if second.onset >= first.end:
                continue
            simultaneous = second.onset == first.onset
            if simultaneous:
                yield self._chord_split(part, measure, staff, voice, first, second)
            else:
                yield self._overrun(part, measure, staff, voice, first, second)

    def _chord_split(
        self,
        part: Part,
        measure: Measure,
        staff: int,
        voice: str,
        first: Event,
        second: Event,
    ) -> Suggestion:
        pitches = self._pitch_label(first), self._pitch_label(second)
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.VOICE,
            severity=Severity.HIGH,
            part=part,
            measure=measure,
            onset=second.onset,
            staff=staff,
            voice=voice,
            refs=(second.ref, first.ref),
            title="Two notes start together in one voice",
            explanation=(
                f"{pitches[0]} and {pitches[1]} both start at this point in voice {voice}, but a "
                "voice can only sound one thing at a time. Either they are a chord that was "
                "split into separate notes, or the second one belongs to another voice."
            ),
            current_repr=f"{pitches[0]} + {pitches[1]} in voice {voice}",
            suggested_repr=f"move {pitches[1]} to its own voice",
            evidence=(
                self.evidence(
                    self.param("chord_split_score"),
                    f"simultaneous events in voice {voice} at onset {second.onset}",
                    onset=float(second.onset),
                ),
            ),
            edits=(SetVoiceOp(ref=second.ref, voice=self._free_voice(measure, voice)),),
        )

    def _overrun(
        self,
        part: Part,
        measure: Measure,
        staff: int,
        voice: str,
        first: Event,
        second: Event,
    ) -> Suggestion:
        overlap = first.end - second.onset
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.DURATION,
            severity=Severity.HIGH,
            part=part,
            measure=measure,
            onset=first.onset,
            staff=staff,
            voice=voice,
            refs=(first.ref, second.ref),
            title="Note lasts into the next one",
            explanation=(
                f"{self._pitch_label(first)} runs {overlap} quarter notes past the start of "
                f"{self._pitch_label(second)} in the same voice. One of the two durations is too "
                "long — most often a flag or a beam that was not counted."
            ),
            current_repr=f"{self._pitch_label(first)} ({first.duration.describe()})",
            suggested_repr="",
            evidence=(
                self.evidence(
                    self.param("duration_overrun_score"),
                    f"overlap of {overlap} quarter notes inside voice {voice}",
                    overlap=float(overlap),
                ),
            ),
        )

    @staticmethod
    def _pitch_label(event: Event) -> str:
        if isinstance(event, Note):
            return event.pitch.name
        if isinstance(event, Chord):
            return "[" + " ".join(note.pitch.name for note in event.notes) + "]"
        return "rest"

    @staticmethod
    def _free_voice(measure: Measure, current: str) -> str:
        used = {int(voice) for voice in measure.voices if voice.isdigit()}
        candidate = 1
        while candidate in used:
            candidate += 1
        return str(candidate) if current.isdigit() else f"{current}b"


class VoiceCrossingDetector(Detector):
    """A voice that briefly crosses above the voice it otherwise stays below.

    Composers do cross voices, so a crossing on its own means nothing. What means something is a
    *single* crossing inside a passage that is otherwise consistently ordered: that is the shape of
    one note assigned to the wrong voice, not of a compositional gesture.
    """

    name = "voice_crossing"
    description = "Isolated voice crossings in otherwise consistently ordered writing"
    kinds = (SuggestionKind.VOICE,)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.6,
        #: How consistently the pair must be ordered elsewhere in the measure, as a fraction.
        "min_consistency": 0.85,
        #: Minimum simultaneities in the measure before the statistic means anything.
        "min_samples": 4.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for part, measure in context.score.iter_measures():
            for staff in sorted({event.staff for event in measure.events}):
                yield from self._check_staff(part, measure, staff)

    def _check_staff(self, part: Part, measure: Measure, staff: int) -> Iterable[Suggestion]:
        voices = sorted(
            {event.voice for event in measure.events if event.staff == staff and event.voice}
        )
        if len(voices) < 2:
            return
        for upper, lower in itertools.pairwise(voices):
            samples = self._compare(measure, staff, upper, lower)
            if len(samples) < self.param("min_samples"):
                continue
            above = sum(1 for _, ordering in samples if ordering > 0)
            below = sum(1 for _, ordering in samples if ordering < 0)
            total = above + below
            if total == 0:
                continue
            majority = max(above, below)
            if majority / total < self.param("min_consistency") or majority == total:
                continue
            minority_sign = 1 if below > above else -1
            outliers = [onset for onset, ordering in samples if ordering == minority_sign]
            if len(outliers) != 1:
                continue
            onset = outliers[0]
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.VOICE,
                severity=Severity.MEDIUM,
                part=part,
                measure=measure,
                onset=onset,
                staff=staff,
                voice=upper if minority_sign > 0 else lower,
                refs=self._refs_at(measure, staff, (upper, lower), onset),
                title=f"Voices {upper} and {lower} cross once here",
                explanation=(
                    f"Voice {upper} sits {'above' if above > below else 'below'} voice {lower} "
                    f"everywhere else in this measure and swaps over at exactly one point. A "
                    "single isolated crossing usually means a note landed in the wrong voice "
                    "during recognition rather than a deliberate crossing."
                ),
                current_repr=f"voices {upper}/{lower} swapped",
                suggested_repr="",
                evidence=(
                    self.evidence(
                        self.param("score"),
                        f"{majority}/{total} simultaneities ordered consistently, one exception",
                        consistency=majority / total,
                        samples=total,
                    ),
                ),
            )

    @staticmethod
    def _compare(
        measure: Measure, staff: int, upper: str, lower: str
    ) -> list[tuple[Fraction, int]]:
        """Sign of (upper voice pitch − lower voice pitch) at each shared onset."""

        def top(events: list[Event]) -> int | None:
            """Highest sounding MIDI number among a voice's events at one onset."""
            values: list[int] = []
            for event in events:
                if isinstance(event, Note):
                    values.append(event.pitch.midi)
                elif isinstance(event, Chord):
                    values.append(event.highest.pitch.midi)
            return max(values) if values else None

        by_onset: dict[Fraction, dict[str, list[Event]]] = defaultdict(lambda: defaultdict(list))
        for event in measure.events:
            if event.staff != staff or event.voice not in {upper, lower}:
                continue
            by_onset[event.onset][event.voice].append(event)

        samples: list[tuple[Fraction, int]] = []
        for onset, voices in sorted(by_onset.items()):
            high = top(voices.get(upper, []))
            low = top(voices.get(lower, []))
            if high is None or low is None or high == low:
                continue
            samples.append((onset, 1 if high > low else -1))
        return samples

    @staticmethod
    def _refs_at(
        measure: Measure, staff: int, voices: tuple[str, str], onset: Fraction
    ) -> tuple[int, ...]:
        return tuple(
            event.ref
            for event in measure.events
            if event.staff == staff and event.voice in voices and event.onset == onset
        )
