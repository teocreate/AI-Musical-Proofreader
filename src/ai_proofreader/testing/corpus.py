"""The reference corpus.

Four pieces, chosen to cover the failure modes that matter and — just as importantly — to include
material the system should stay *quiet* about:

``chorale``     four-part homophony. Vertical harmony, transposition-free, dense simultaneities.
``piano``       two staves, one voice each, a motif stated six times. The pattern detector's home.
``trio``        flute, B-flat clarinet and cello: transposing parts, mixed clefs, mixed registers.
``chromatic``   a deliberately chromatic étude with no repeats. The negative control — every
                suggestion produced here is a false positive, because nothing is wrong with it.

Written in the pattern language from :mod:`ai_proofreader.testing.builder`, so the music is
readable in the source and diffs are meaningful when it changes.
"""

from __future__ import annotations

from .builder import MeasureSpec, PartSpec, ScoreSpec

__all__ = ["CORPUS", "build_corpus_score", "corpus_names"]


def _chorale() -> ScoreSpec:
    """Eight bars of four-part writing in D major, one chord per half note."""
    soprano = [
        "A4:2 C#5:2", "D5:2 B4:2", "A4:2 C#5:2", "D5:4",
        "B4:2 A4:2", "G4:2 A4:2", "F#4:2 E4:2", "D5:4",
    ]  # fmt: skip
    alto = [
        "F#4:2 E4:2", "F#4:2 G4:2", "F#4:2 E4:2", "A4:4",
        "G4:2 F#4:2", "E4:2 E4:2", "D4:2 C#4:2", "A4:4",
    ]  # fmt: skip
    tenor = [
        "D4:2 A3:2", "B3:2 D4:2", "D4:2 A3:2", "F#4:4",
        "D4:2 D4:2", "B3:2 C#4:2", "A3:2 A3:2", "F#4:4",
    ]  # fmt: skip
    bass = [
        "D3:2 A2:2", "B2:2 G2:2", "D3:2 A2:2", "D3:4",
        "G2:2 D3:2", "E3:2 A2:2", "D3:2 A2:2", "D3:4",
    ]  # fmt: skip

    def part(part_id: str, name: str, patterns: list[str], clef: tuple[str, int]) -> PartSpec:
        measures = [MeasureSpec.single(pattern) for pattern in patterns]
        measures[0] = MeasureSpec(voices={"1": patterns[0]}, clef=clef)
        measures[-1] = MeasureSpec(voices={"1": patterns[-1]}, repeat_backward=False)
        return PartSpec(id=part_id, name=name, measures=measures)

    return ScoreSpec(
        title="Chorale in D",
        composer="Test corpus",
        key_fifths=2,
        time=(4, 4),
        parts=[
            part("P1", "Soprano", soprano, ("G", 2)),
            part("P2", "Alto", alto, ("G", 2)),
            part("P3", "Tenor", tenor, ("F", 4)),
            part("P4", "Bass", bass, ("F", 4)),
        ],
    )


def _piano() -> ScoreSpec:
    """Sixteen bars in G major with one figure stated six times.

    The motif keeps its rhythm and its staff positions across every statement, which is what makes
    a single-note deviation in any one copy detectable.
    """
    motif = "G4:1/2 A4:1/2 B4:1 D5:1 B4:1"
    answer = "C5:1/2 D5:1/2 E5:1 G5:1 E5:1"
    cadence = "A4:1 F#4:1 G4:2"
    closing = "D5:1 B4:1 A4:1 F#4:1"
    right = [
        motif, answer, motif, cadence,
        motif, answer, motif, cadence,
        motif, answer, motif, cadence,
        closing, "E5:1 C5:1 B4:1 G4:1", "A4:1 F#4:1 D4:1 F#4:1", "G4:4",
    ]  # fmt: skip

    tonic = "G2:1 D3:1 B2:1 D3:1"
    subdominant = "C3:1 G3:1 E3:1 G3:1"
    dominant = "D3:1 A3:1 F#3:1 A3:1"
    left = [
        tonic, subdominant, tonic, dominant,
        tonic, subdominant, tonic, dominant,
        tonic, subdominant, tonic, dominant,
        tonic, subdominant, dominant, "G2:4",
    ]  # fmt: skip

    measures: list[MeasureSpec] = []
    for index, (upper, lower) in enumerate(zip(right, left, strict=True)):
        measures.append(
            MeasureSpec(
                voices={"1": upper, "2": lower},
                staff_of_voice={"1": 1, "2": 2},
                dynamics={"0": "mf"} if index == 0 else {},
            )
        )
    return ScoreSpec(
        title="Study in G",
        composer="Test corpus",
        key_fifths=1,
        time=(4, 4),
        parts=[PartSpec(id="P1", name="Piano", staves=2, measures=measures)],
    )


def _trio() -> ScoreSpec:
    """Flute, B-flat clarinet and cello — transposition and mixed clefs in one score.

    The clarinet part is *written* a major second above what it sounds, so any analysis that
    forgets to transpose will read its harmony a whole tone out and produce nonsense. That is
    exactly what this entry is here to catch.
    """
    flute = [
        "F5:1 E5:1 D5:2", "C5:1 D5:1 E5:2", "F5:1 G5:1 A5:2", "G5:1 F5:1 E5:2",
        "F5:1 E5:1 D5:2", "C5:1 B4:1 C5:2", "D5:1 E5:1 F5:2", "E5:4",
    ]  # fmt: skip
    # Sounding a whole tone lower: written D5 sounds C5.
    clarinet = [
        "A5:2 G5:2", "A5:2 G5:2", "C6:2 B5:2", "B5:2 A5:2",
        "A5:2 G5:2", "A5:2 G5:2", "B5:2 C6:2", "B5:4",
    ]  # fmt: skip
    cello = [
        "F3:1 C3:1 F3:2", "A2:1 E3:1 A2:2", "F3:1 C3:1 F3:2", "C3:1 G3:1 C3:2",
        "F3:1 C3:1 F3:2", "A2:1 E3:1 A2:2", "Bb2:1 F3:1 Bb2:2", "C3:4",
    ]  # fmt: skip

    return ScoreSpec(
        title="Trio movement",
        composer="Test corpus",
        key_fifths=-1,
        time=(4, 4),
        parts=[
            PartSpec(
                id="P1",
                name="Flute",
                measures=[MeasureSpec.single(pattern) for pattern in flute],
            ),
            PartSpec(
                id="P2",
                name="Clarinet in B-flat",
                transpose=(-1, -2, 0),
                # Sounds a major second below written, so concert F major is written in G major.
                key_fifths=1,
                measures=[MeasureSpec.single(pattern) for pattern in clarinet],
            ),
            PartSpec(
                id="P3",
                name="Violoncello",
                measures=[
                    MeasureSpec(voices={"1": pattern}, clef=("F", 4) if index == 0 else None)
                    for index, pattern in enumerate(cello)
                ],
            ),
        ],
    )


def _chromatic() -> ScoreSpec:
    """A chromatic étude with no repeated figures — the negative control.

    Nothing in this piece is wrong. Every suggestion the system produces here is a false positive,
    which makes it the single most useful entry in the corpus: it measures the cost of every rule
    directly, in the units the user cares about.
    """
    lines = [
        "C4:1/2 C#4:1/2 D4:1/2 D#4:1/2 E4:1/2 F4:1/2 F#4:1/2 G4:1/2",
        "Ab4:1/2 G4:1/2 F#4:1/2 F4:1/2 E4:1/2 Eb4:1/2 D4:1/2 Db4:1/2",
        "C4:1 E4:1/2 G#4:1/2 B4:1 D#5:1",
        "E5:1/2 Eb5:1/2 D5:1/2 Db5:1/2 C5:1/2 B4:1/2 Bb4:1/2 A4:1/2",
        "Ab4:1 F4:1 Db4:1 Bb3:1",
        "A3:1/2 C#4:1/2 F4:1/2 A4:1/2 C#5:1/2 F5:1/2 A5:1/2 C#6:1/2",
        "B5:1 G#5:1 E5:1 C#5:1",
        "A4:2 G#4:2",
    ]
    return ScoreSpec(
        title="Chromatic study",
        composer="Test corpus",
        key_fifths=0,
        time=(4, 4),
        parts=[
            PartSpec(
                id="P1",
                name="Piano",
                measures=[MeasureSpec.single(pattern) for pattern in lines],
            )
        ],
    )


#: Name → builder. Kept as callables so a corpus entry is only constructed when it is asked for.
CORPUS = {
    "chorale": _chorale,
    "piano": _piano,
    "trio": _trio,
    "chromatic": _chromatic,
}


def corpus_names() -> tuple[str, ...]:
    return tuple(CORPUS)


def build_corpus_score(name: str) -> ScoreSpec:
    """Build one corpus entry by name."""
    try:
        return CORPUS[name]()
    except KeyError:
        raise KeyError(f"unknown corpus entry {name!r}; have {', '.join(CORPUS)}") from None
