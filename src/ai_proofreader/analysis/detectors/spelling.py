"""Enharmonic-spelling detector.

The first real score in the corpus — a Schmitt sonatina movement recognized by MuseScore — had
one misread pitch and *fourteen* misspelled ones. The engine heard every sound correctly and
chose the wrong letter for it: C-sharp where the music says D-flat, F-sharp where it says G-flat.

That is a class of error no other rule here can see, because nothing about it is wrong
*acoustically*. A G-flat major triad written F-sharp / B-flat / D-flat sounds exactly like a
G-flat major triad. It is only wrong on the page, and only a musician notices — which is
precisely the kind of thing a proofreader is for.

Three signals decide it, in order of strength:

1. **Resolution.** An altered note that rises a semitone is spelled sharp; one that falls a
   semitone is spelled flat. C-sharp goes to D, D-flat goes to C. Agreement here is a veto — a
   rising sharp is right whatever else says otherwise — and disagreement is the strongest
   evidence available.
2. **Distance from the key**, on the circle of fifths. In F major (−1), D-flat sits 4 steps away
   and C-sharp sits 8. A spelling that far out, when its twin is that close, is not the one an
   engraver used.
3. **The vertical spelling**: notes engraved together lie in a compact window of the line of
   fifths, and a respelling that pulls a wide chord back together is corroboration from the
   harmony rather than from the key.

Measured against the Schmitt score with the original scan as the arbiter — not the recognized
file, and not my own opinion — this finds **13 of the 15** misspellings, with one false positive
in m.22, where the engraver really did print a sharp in a diminished seventh that resolves as if
it were a flat. Of the two misses, one (m.14) is a D-flat that rises to D-natural, which the
resolution veto suppresses by design; the other sits one fifth under the threshold. Both are the
price of a rule that says nothing on the human-corrected copy of the same piece, which is the
property that matters: a proofreader that talks on clean music is a proofreader people switch off.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...models import (
    ALTER_ACCIDENTAL,
    Channel,
    KeySignature,
    Note,
    Pitch,
    SetPitchOp,
    Severity,
    Suggestion,
    SuggestionKind,
)
from ...music_theory import circle_of_fifths_position, nearest_spelling, spelling_distance
from ..base import Detector, make_suggestion
from ..context import AnalysisContext, NoteRef, StreamEntry

__all__ = ["EnharmonicSpellingDetector"]


def _notes_of(entry: StreamEntry) -> tuple[Note, ...]:
    """Every sounding note in a stream slot, chord members included."""
    if entry.chord is not None:
        return tuple(entry.chord.notes)
    return (entry.note,) if entry.note is not None else ()


def _matches_key_flavour(spelling: Pitch, key: KeySignature) -> bool:
    """Whether a proposed accidental is the kind this key signature already uses.

    Distance on the circle of fifths is not symmetric around a key: the seven natural letters
    occupy positions −1 to 5, so a sharp always measures further from the key number than the
    flat that sounds the same, whatever the key. Read literally, the measure would recommend
    flattening every sharp in C major — and a C-major augmented triad is spelled C–E–G-sharp by
    every engraver alive.

    The asymmetry is repaired by only proposing accidentals of the kind the key already uses:
    flats in flat keys, sharps in sharp keys, and — without corroboration from somewhere else —
    nothing at all in C major, where the measure carries no information. Respellings that remove
    an accidental entirely (B-sharp to C) are always allowed; they simplify the page.
    """
    if spelling.alter == 0:
        return True
    if key.fifths == 0:
        return False
    return (spelling.alter < 0) == (key.fifths < 0)


class EnharmonicSpellingDetector(Detector):
    """Notes whose sound is right but whose letter name is not."""

    name = "enharmonic_spelling"
    description = "Notes spelled far from their key when the enharmonic twin sits close"
    kinds = (SuggestionKind.PITCH,)
    channel = Channel.MUSICAL
    defaults = {
        "score": 0.72,
        #: How much closer to the key the alternative spelling must sit, in fifths. Three is
        #: measured, not guessed: at two, precision collapses on the reference score.
        "min_fifths_gain": 3.0,
        #: The gain required when the notes sounding *with* it independently agree. See
        #: ``_vertical_support`` for what that means.
        "min_fifths_gain_with_chord": 2.0,
        #: The gain required when the note's own resolution contradicts its accidental — see
        #: ``_resolutions``. This is the strongest of the three signals and gets the lowest bar.
        "min_fifths_gain_with_resolution": 2.0,
        #: Confidence bonus when the vertical spelling corroborates the respelling.
        "chord_bonus": 0.12,
        #: Confidence bonus when the resolution corroborates it.
        "resolution_bonus": 0.14,
        #: How much tighter the simultaneity's spelling must become, in fifths, to count as
        #: corroboration. Two keeps it to cases where the respelling visibly repairs the chord.
        "min_span_gain": 2.0,
    }

    def run(self, context: AnalysisContext) -> Iterable[Suggestion]:
        supported = self._vertical_support(context)
        resolutions = self._resolutions(context)
        for ref in context.note_refs:
            note = ref.note
            if note.pitch.alter == 0:
                continue  # a natural has no enharmonic worth proposing
            if note.is_grace:
                continue

            key = ref.measure.attributes.key_for_staff(note.staff)
            better = nearest_spelling(note.pitch, key)
            if better.step is note.pitch.step:
                continue

            implied = 1 if note.pitch.alter > 0 else -1
            resolution = resolutions.get(id(note), 0)
            if resolution == implied:
                # The accidental is doing its job: a sharp that rises a semitone is a leading
                # tone and a flat that falls is an appoggiatura. Respelling either would be
                # wrong however far the key sits, so this outranks every other signal.
                continue

            chord = id(note) in supported
            voice_leading = resolution == -implied
            if not (chord or voice_leading) and not _matches_key_flavour(better, key):
                continue

            gain = spelling_distance(note.pitch, key) - spelling_distance(better, key)
            required = self.param("min_fifths_gain")
            if voice_leading:
                required = min(required, self.param("min_fifths_gain_with_resolution"))
            if chord:
                required = min(required, self.param("min_fifths_gain_with_chord"))
            if gain < required:
                continue

            yield self._report(ref, note, better, key, gain, chord, voice_leading)

    # -- corroboration -------------------------------------------------------------

    def _vertical_support(self, context: AnalysisContext) -> set[int]:
        """Notes whose respelling also tightens the chord they sound in.

        Chord *identity* cannot help here — C-sharp and D-flat are the same pitch class, so every
        template match is identical before and after. What does change is how the simultaneity
        reads on the page. Notes engraved together lie in a compact window of the line of fifths:
        G-flat / B-flat / D-flat spans four, while the same sound written F-sharp / B-flat /
        D-flat spans eleven. A respelling that pulls a wide-spanning chord back together is
        evidence from the harmony rather than from the key signature, which is what makes it
        worth combining with the key-distance signal.
        """
        threshold = self.param("min_span_gain")
        supported: set[int] = set()
        for slice_ in context.vertical_slices:
            if len(slice_.notes) < 3:
                continue
            positions = [circle_of_fifths_position(ref.note.pitch) for ref in slice_.notes]
            before = max(positions) - min(positions)
            if before <= threshold:
                continue
            for index, candidate in enumerate(slice_.notes):
                pitch = candidate.note.pitch
                if pitch.alter == 0:
                    continue
                key = candidate.measure.attributes.key_for_staff(candidate.note.staff)
                better = nearest_spelling(pitch, key)
                if better.step is pitch.step:
                    continue
                after = self._span_with(positions, index, circle_of_fifths_position(better))
                if before - after >= threshold:
                    supported.add(id(candidate.note))
        return supported

    @staticmethod
    def _span_with(positions: Sequence[int], index: int, replacement: int) -> int:
        """Line-of-fifths span of ``positions`` with one entry swapped out."""
        swapped = list(positions)
        swapped[index] = replacement
        return max(swapped) - min(swapped)

    @staticmethod
    def _resolutions(context: AnalysisContext) -> dict[int, int]:
        """Which way each altered note moves next: ``+1`` up a semitone, ``-1`` down, ``0`` else.

        This is the oldest rule in the book and the one that decides these cases: an altered note
        that rises a semitone is spelled sharp, one that falls a semitone is spelled flat. C-sharp
        goes to D; D-flat goes to C. They are the same key on a piano and opposite meanings on
        the page.

        Both directions are used. Agreement is a veto — a rising sharp is right no matter what
        the key signature says — and disagreement is the strongest evidence this detector has,
        which is why it earns the lowest threshold.
        """
        directions: dict[int, int] = {}
        for stream in context.streams:
            entries = [entry for entry in stream.entries if not entry.is_rest]
            for index, entry in enumerate(entries[:-1]):
                targets = {note.pitch.midi for note in _notes_of(entries[index + 1])}
                if not targets:
                    continue
                for note in _notes_of(entry):
                    if note.pitch.alter == 0:
                        continue
                    up = note.pitch.midi + 1 in targets
                    down = note.pitch.midi - 1 in targets
                    if up != down:  # ambiguous when the next chord contains both neighbours
                        directions[id(note)] = 1 if up else -1
        return directions

    # -- reporting -----------------------------------------------------------------

    def _report(
        self,
        ref: NoteRef,
        note: Note,
        better: Pitch,
        key: KeySignature,
        gain: int,
        chord: bool,
        voice_leading: bool,
    ) -> Suggestion:
        direction = "down" if note.pitch.alter > 0 else "up"
        reasons = [f"which sits {gain} steps closer to {key.describe()} on the circle of fifths"]
        if voice_leading:
            reasons.append(
                f"and the note moves {direction} a semitone, which is what "
                f"{'a flat' if better.alter < 0 else 'a sharp'} does"
            )
        if chord:
            reasons.append("and respelling it brings the notes sounding with it into one key")
        score = min(
            1.0,
            self.param("score")
            + (self.param("chord_bonus") if chord else 0.0)
            + (self.param("resolution_bonus") if voice_leading else 0.0),
        )
        reason = " ".join(reasons)
        return make_suggestion(
            detector=self.name,
            kind=SuggestionKind.PITCH,
            severity=Severity.MEDIUM,
            part=ref.part,
            measure=ref.measure,
            onset=note.onset,
            staff=note.staff,
            voice=note.voice,
            refs=(note.ref,),
            title=f"{note.pitch.name} is probably spelled {better.name}",
            explanation=(
                f"This note sounds correct but is written with the wrong letter. In "
                f"{key.describe()} an engraver would write {better.name}, {reason}. The sound "
                "does not change — only how it reads on the page, which is what a player "
                "follows."
            ),
            current_repr=note.pitch.name,
            suggested_repr=better.name,
            evidence=(
                self.evidence(
                    score,
                    f"{note.pitch.name} sits {spelling_distance(note.pitch, key)} fifths from "
                    f"{key.describe()}; {better.name} sits {spelling_distance(better, key)}",
                    gain=gain,
                    chord_support=chord,
                    resolution_support=voice_leading,
                ),
            ),
            edits=(
                SetPitchOp(
                    ref=note.ref,
                    step=better.step,
                    alter=better.alter,
                    octave=better.octave,
                    accidental=ALTER_ACCIDENTAL.get(better.alter),
                    # Always printed. A respelled note carries no accidental over from earlier
                    # in the bar — that carry-over belonged to the old letter — so D-flat needs
                    # its own flat even where the C-sharp it replaces did not need a sharp.
                    set_accidental=True,
                ),
            ),
        )
