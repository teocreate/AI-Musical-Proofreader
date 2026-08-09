"""Pitch detectors driven by melodic contour and vertical harmony.

Both rules here work the same way: propose the smallest possible change and measure whether it
makes the music markedly more ordinary. That framing keeps them honest. A rule that asks "is this
note strange?" fires constantly on real repertoire; a rule that asks "is there a one-step change
that removes a specific anomaly?" fires only when it has something useful to say.
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median

from ...models import (
    ALTER_ACCIDENTAL,
    Channel,
    Note,
    Pitch,
    SetPitchOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ...music_theory import chord_fit, identify_chord, local_roughness, spike_analysis
from ..base import Detector, make_suggestion
from ..context import AnalysisContext, StreamEntry, VerticalSlice, VoiceStream

__all__ = ["ContourSpikeDetector", "HarmonicOutlierDetector"]


class ContourSpikeDetector(Detector):
    """A single note that breaks an otherwise smooth melodic line.

    Reading a notehead one line or space off is the archetypal OMR pitch error, and it always
    leaves the same trace: two awkward intervals where there were none, repaired by moving that
    one note back. The rule requires the note to be rough *relative to its own phrase*, not
    against an absolute threshold — a Webern line and a Mozart line have very different baselines.

    **Off by default.** On the first real score in the corpus it produced three suggestions, all
    wrong, and missed the one genuine misread pitch — which was a third, not a spike, and so left
    no roughness to detect. The premise is that composers write smooth lines and OMR breaks them;
    real keyboard writing is full of deliberate leaps that are smooth in *voice-leading* terms
    while looking rough to an interval-by-interval measure. Until the rule reasons about the
    implied voices rather than the printed sequence, it costs more than it returns.

    Enable it with ``{"detectors": {"contour_spike": {}}}``.
    """

    name = "contour_spike"
    description = "Notes that break an otherwise smooth melodic line and are fixed by one step"
    kinds = (SuggestionKind.PITCH,)
    channel = Channel.MUSICAL
    enabled_by_default = False
    defaults = {
        "score": 0.58,
        #: Minimum fraction of the local roughness that the repair must remove.
        "min_improvement": 0.6,
        #: How many times rougher than the phrase's median the note must be.
        "roughness_ratio": 2.5,
        #: Shortest phrase worth judging: below this there is no baseline to be rough against.
        "min_run_length": 6.0,
        #: Bonus when the repaired pitch is diatonic and the original was not.
        "diatonic_bonus": 0.14,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        for stream in context.streams:
            for run in stream.melodic_runs(min_length=int(self.param("min_run_length"))):
                yield from self._check_run(context, stream, run)

    def _check_run(
        self, context: AnalysisContext, stream: VoiceStream, run: list[StreamEntry]
    ) -> Iterable[Suggestion]:
        pitches = [entry.note.pitch for entry in run if entry.note is not None]
        if len(pitches) != len(run):
            return
        roughness_values = [local_roughness(pitches, index) for index in range(len(pitches))]
        baseline = median(roughness_values) if roughness_values else 0.0

        for index in range(1, len(pitches) - 1):
            if baseline > 0 and roughness_values[index] < baseline * self.param("roughness_ratio"):
                continue
            anomaly = spike_analysis(pitches, index)
            if anomaly is None or anomaly.improvement < self.param("min_improvement"):
                continue

            entry = run[index]
            note = entry.note
            if note is None or note.is_grace:
                continue
            repaired = pitches[index].step_shifted(anomaly.step_offset, alter=0)
            repaired = self._respell(context, stream, entry, repaired)

            local = context.local_key(stream.part, entry.measure.index)
            diatonic_gain = 0.0
            if local.is_reliable:
                classes = local.pitch_classes()
                original_in = stream.part.transpose.apply(note.pitch).pitch_class in classes
                repaired_in = stream.part.transpose.apply(repaired).pitch_class in classes
                if repaired_in and not original_in:
                    diatonic_gain = self.param("diatonic_bonus")
                elif original_in and not repaired_in:
                    continue  # the "repair" would take a diatonic note out of the key

            score = min(
                1.0,
                self.param("score") * (0.6 + 0.4 * anomaly.improvement) + diatonic_gain,
            )
            yield make_suggestion(
                detector=self.name,
                kind=SuggestionKind.PITCH,
                severity=Severity.MEDIUM,
                part=stream.part,
                measure=entry.measure,
                onset=note.onset,
                staff=note.staff,
                voice=note.voice,
                refs=(note.ref,),
                title=f"{note.pitch.name} breaks the melodic line",
                explanation=(
                    f"The line steps smoothly either side of this note, which leaps away and "
                    f"straight back. Moving it {abs(anomaly.step_offset)} staff position(s) "
                    f"{'up' if anomaly.step_offset > 0 else 'down'} to {repaired.name} removes "
                    f"{anomaly.improvement:.0%} of the awkwardness — the signature of a notehead "
                    "read on the wrong line."
                ),
                current_repr=note.pitch.name,
                suggested_repr=repaired.name,
                evidence=(
                    self.evidence(
                        score,
                        f"contour improves {anomaly.improvement:.0%} by moving to {repaired.name}",
                        improvement=anomaly.improvement,
                        offset=anomaly.step_offset,
                    ),
                ),
                edits=(
                    SetPitchOp(
                        ref=note.ref,
                        step=repaired.step,
                        alter=repaired.alter,
                        octave=repaired.octave,
                        accidental=(
                            ALTER_ACCIDENTAL.get(repaired.alter)
                            if repaired.alter and note.accidental is not None
                            else None
                        ),
                        set_accidental=note.accidental is not None,
                    ),
                ),
            )

    @staticmethod
    def _respell(
        context: AnalysisContext, stream: VoiceStream, entry: StreamEntry, pitch: Pitch
    ) -> Pitch:
        """Give the moved notehead the alteration its new staff position gets from the key."""
        key = entry.measure.attributes.key_for_staff(stream.staff)
        return pitch.with_alter(key.alter_for(pitch.step))


class HarmonicOutlierDetector(Detector):
    """One note in a simultaneity that a semitone move would turn into a recognizable chord."""

    name = "harmonic_outlier"
    description = "Notes clashing with the chord under them, repairable by one semitone"
    cross_part = True
    kinds = (SuggestionKind.ACCIDENTAL_CHANGE, SuggestionKind.PITCH)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.55,
        #: Chord-fit below which a simultaneity counts as unexplained.
        "max_current_fit": 0.62,
        #: Chord-fit the repair must reach.
        "min_repaired_fit": 0.9,
        #: Minimum improvement, so we do not rewrite passing dissonance.
        "min_gain": 0.3,
        #: Simultaneities shorter than this are passing motion, not harmony.
        "min_duration": 0.5,
        #: Parts that must be sounding before a vertical reading means anything.
        "min_voices": 3.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        if len(context.score.parts) == 0:
            return
        minimum_voices = int(self.param("min_voices"))
        for index, slice_ in enumerate(context.vertical_slices):
            if len(slice_.notes) < minimum_voices:
                continue
            duration = self._slice_duration(context, index)
            if duration < self.param("min_duration"):
                continue
            classes = slice_.sounding_pitch_classes
            if len(classes) < 3:
                continue
            current = chord_fit(classes)
            if current > self.param("max_current_fit"):
                continue
            yield from self._repairs(context, slice_, classes, current)

    @staticmethod
    def _slice_duration(context: AnalysisContext, index: int) -> float:
        slices = context.vertical_slices
        if index + 1 < len(slices):
            return float(slices[index + 1].onset - slices[index].onset)
        return float(
            max((ref.end for ref in slices[index].notes), default=slices[index].onset)
            - slices[index].onset
        )

    def _repairs(
        self,
        context: AnalysisContext,
        slice_: VerticalSlice,
        classes: frozenset[int],
        current_fit: float,
    ) -> Iterable[Suggestion]:
        counts: dict[int, int] = {}
        for ref in slice_.notes:
            counts[ref.sounding.pitch_class] = counts.get(ref.sounding.pitch_class, 0) + 1

        for ref in slice_.notes:
            note = ref.note
            if note.is_grace:
                continue
            pitch_class = ref.sounding.pitch_class
            if counts[pitch_class] > 1:
                continue  # a doubled note is corroborated by its double; leave it alone
            others = frozenset(classes - {pitch_class})

            best: tuple[Pitch, float, bool] | None = None
            for candidate, is_accidental in self._candidates(ref):
                candidate_class = ref.part.transpose.apply(candidate).pitch_class
                if candidate_class in others or candidate_class == pitch_class:
                    continue
                fit = chord_fit(others | {candidate_class})
                if best is None or fit > best[1]:
                    best = (candidate, fit, is_accidental)
            if best is None:
                continue

            repaired_written, repaired_fit, is_accidental = best
            if repaired_fit < self.param(
                "min_repaired_fit"
            ) or repaired_fit - current_fit < self.param("min_gain"):
                continue

            repaired_class = ref.part.transpose.apply(repaired_written).pitch_class
            match = identify_chord(others | {repaired_class})
            yield self._report(
                ref, note, repaired_written, match, current_fit, repaired_fit, is_accidental
            )

    @staticmethod
    def _candidates(ref: object) -> list[tuple[Pitch, bool]]:
        """Single-symbol misreadings of one note, as (pitch, is_accidental_error) pairs.

        Two distinct OMR failure modes produce two distinct candidate sets, and conflating them
        loses half the recall: a missed accidental moves the note by a semitone while leaving the
        notehead where it is, and a misread notehead moves it to the adjacent line or space —
        which is one *or two* semitones depending on where in the scale it lands.
        """
        note = ref.note  # type: ignore[attr-defined]
        measure = ref.measure  # type: ignore[attr-defined]
        key = measure.attributes.key_for_staff(note.staff)
        candidates: list[tuple[Pitch, bool]] = []
        for delta in (-1, 1):
            altered = note.pitch.with_alter(note.pitch.alter + delta)
            if abs(altered.alter) <= 2:
                candidates.append((altered, True))
        for offset in (-1, 1):
            shifted = note.pitch.step_shifted(offset, alter=0)
            candidates.append((shifted.with_alter(key.alter_for(shifted.step)), False))
        return candidates

    def _report(
        self,
        ref: object,
        note: Note,
        repaired: Pitch,
        match: object,
        current_fit: float,
        repaired_fit: float,
        is_accidental: bool,
    ) -> Suggestion:
        chord_name = getattr(match, "label", "a clearer chord")
        cause = (
            "A misread or missing accidental is the usual cause."
            if is_accidental
            else "A notehead read onto the neighbouring line or space is the usual cause."
        )
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.ACCIDENTAL_CHANGE if is_accidental else SuggestionKind.PITCH,
            severity=Severity.MEDIUM,
            part=ref.part,  # type: ignore[attr-defined]
            measure=ref.measure,  # type: ignore[attr-defined]
            onset=note.onset,
            staff=note.staff,
            voice=note.voice,
            refs=(note.ref,),
            title=f"{note.pitch.name} clashes with the chord here",
            explanation=(
                f"The notes sounding together at this point do not form a chord anyone would "
                f"write. Changing this one note to {repaired.name} produces {chord_name}. " + cause
            ),
            current_repr=note.pitch.name,
            suggested_repr=repaired.name,
            evidence=(
                self.evidence(
                    min(1.0, self.param("score") * (0.7 + 0.3 * (repaired_fit - current_fit))),
                    f"chord fit rises from {current_fit:.2f} to {repaired_fit:.2f} "
                    f"({chord_name})",
                    before=current_fit,
                    after=repaired_fit,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=repaired.step,
                    alter=repaired.alter,
                    octave=repaired.octave,
                    accidental=ALTER_ACCIDENTAL.get(repaired.alter) if is_accidental else None,
                    set_accidental=is_accidental,
                ),
            ),
        )
