# AI Musical Proofreader

[![CI](https://github.com/teocreate/AI-Musical-Proofreader/actions/workflows/ci.yml/badge.svg)](https://github.com/teocreate/AI-Musical-Proofreader/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Licence: AGPL v3](https://img.shields.io/badge/licence-AGPL--3.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-253-brightgreen)](tests/)

**A spell checker for sheet music.** Optical music recognition gets you 95% of the way; this tool
is about the last 5% — finding the notes your OMR engine got wrong, in the file it just produced,
without you having to compare every bar against the scan by eye.

It reads MusicXML or MSCZ that MuseScore (or any OMR engine) produced, treats it as a *hypothesis*
rather than a result, and reports the notes it does not believe — each with a reason you can check
in five seconds and a one-click fix.

It never changes your score on its own.

```
[ 85%] high     Piano m. 18 (staff 1, voice 1)
    C#5 is probably spelled Db5
    C#5 -> Db5
    This note sounds correct but is written with the wrong letter. In F major an engraver
    would write Db5, which sits 4 steps closer to F major on the circle of fifths and the
    note moves down a semitone, which is what a flat does. The sound does not change — only
    how it reads on the page, which is what a player follows.
```

---

## The problem

Digitising sheet music is two jobs, and only one of them has been automated.

The first job is recognition: turn pixels into notes. MuseScore, Audiveris, homr and the
commercial engines all do this, and they do it well enough to be useful — typically 90–98% of
notes correct on clean printed music.

The second job is **proofreading**: find the 2–10% that are wrong. Nobody has automated this, so
every musician who digitises a score does it the same way: put the scan on one screen, the
recognised file on the other, and read both, bar by bar, for an hour or three. The error rate of
that process is roughly the error rate of human attention at bar 340 of a Brahms sonata.

The asymmetry is what makes it worth attacking. Recognition is a perception problem and it is
crowded. Proofreading is a *reasoning* problem — most OMR mistakes leave a trace in the music
itself, and the ones that don't can be checked against a specific region of the page rather than
the whole thing — and almost nobody is working on it.

A concrete example, from the first real score in this repository's evaluation set (Jacob Schmitt,
Sonatina op. 207 no. 2, mvt II, recognised by MuseScore 4):

| | |
|---|---|
| Notes | 249 |
| Wrong pitch (would sound wrong) | 1 |
| Wrong spelling (sounds right, reads wrong) | 15 |
| Structural errors (a held note split into tied chord members) | 5 bars |

Fifteen of the seventeen problems in that file were *notation* errors, not audio errors. Play the
file back and it sounds perfect. Hand it to a pianist and they stumble, because the middle section
is written in D-flat and the file says C-sharp. No playback test catches this. No OMR benchmark
measures it. It is exactly what a proofreader is for.

## What this is, and what it is not

**It is not an OMR engine.** It does not look for staves, does not classify noteheads, and would
be a bad tool for that. It starts where OMR stops.

**It is not an autocorrect.** Everything it finds is a suggestion, shown with its evidence, and
applied only when you press Accept. The one thing a tool like this must never do is silently
change a score, because then you have to proofread it anyway — and now you don't know what
changed.

**It is a reasoning layer** with three channels of evidence:

1. **Musical** (built) — what the file says about itself. Bars that don't add up, ties joining
   different pitches, a repeated figure whose copies disagree at exactly one note, accidentals
   contradicting the measure they're in, notes spelled far from their own key.
2. **Visual** (specified, not built) — what the page says. Given a suspect note, align it to its
   pixels and ask whether the notehead really sits on that line.
3. **Multimodal** (specified, not built) — a vision model as an adjudicator for the cases the
   first two disagree about, not as a first-pass detector.

The confidence layer that fuses them is already written and already caps musical-only findings at
85%, because theory alone should not claim certainty about a page it has not seen.

## Philosophy

These are not aspirations; they are constraints that have already caused code to be deleted.

**Precision is the product.** A proofreader that flags 400 things on a 30-page score has made your
job worse, not better. The failure mode of this category of tool is not missing an error — you
were going to check the score anyway — it is crying wolf until the user stops reading the panel.
Every rule is measured against correct music before it is measured against broken music.

**A rule that fires on correct music gets switched off.** Two rules in this repository are
disabled by default (`chromatic_outlier`, `contour_spike`) because on the first real score they
produced fourteen false positives and zero true ones. They were convincing on synthetic data. They
kept their code, their tests and their documentation, and lost their default. This is the single
most important habit in the project.

**Show the reasoning, always.** Every suggestion carries the evidence that produced it, in
language a musician reads rather than a developer. "3 other occurrences of this figure read F#5"
is checkable in seconds. "confidence 0.87" is not.

**Say what you don't know.** Rules that can detect a problem but cannot identify *which* note
caused it say so, and their Accept button is disabled. A bar that is short by half a beat with two
notes that could each absorb it produces a warning, not a guess.

**Corrections are patches, not re-exports.** The original XML tree stays alive; every note in the
internal model holds a handle back to its element. Accepting a pitch fix produces a one-line diff
— your page layout, credits, fingerings and engraving hints come back untouched. Round-tripping
through an internal model would silently destroy all of it, which is why most tools that touch
MusicXML are unusable on real engraving work.

**Measure on real scores, and publish the losses.** See below. The synthetic corpus in this
repository is where rules are debugged; it is not where they are judged.

## Honest numbers

**On the synthetic regression corpus** (`scripts/build_datasets.py` → `scripts/evaluate.py`,
1019 notes, 57 injected errors):

| Threshold | Reported | Correct | Precision | Recall |
|---|---|---|---|---|
| 0.35 | 27 | 24 | 89% | 42% |
| **0.60** | **25** | **23** | **92%** | **40%** |
| 0.70 | 9 | 9 | 100% | 16% |

Those figures use measure-level matching: right part, right bar, and a diagnosis consistent with
the error. Under exact-onset matching — the suggestion must point at the precise note — precision
at 0.60 is **56%**. Both tables ship in the harness. Neither is the whole truth: onset matching is
systematically pessimistic here, because injecting a duration error shifts every later onset in
its bar so the ground truth no longer lines up, but a 36-point gap is still a real weakness and
not a rounding difference. Under both standards, landing on the right note with the wrong
diagnosis counts as a miss.

**On real material** the picture is different, and worse in ways worth stating plainly. The scan,
the raw OMR output, the human correction and the ground truth all ship in
[`datasets/real/schmitt-op207-2-ii/`](datasets/real/schmitt-op207-2-ii/), so this is reproducible
rather than assertable:

```bash
python scripts/evaluate_real.py
```

| | |
|---|---|
| Wrong spellings found | **13 of 15** |
| Wrong pitches found | **0 of 1** |
| False positives, judged against the scan | 1 (m.22) |
| Suggestions still unattributed on the raw file | 5 |
| Suggestions on the human-corrected copy | 10 (target: 0) |

Four things in that table are worth more than the recall figure.

**The one audible error was invisible.** A G5 read where the page prints F5, and it leaves no
musical trace at all: only two notes sound at that moment, the interval is a third rather than a
spike, and the figure never repeats identically. No amount of rule-writing finds it. It needs the
page, which is Phase 2 — this is the clearest evidence in the project that Phase 2 is not
optional.

**False positives are judged against the scan, not against the human.** The m.22 suggestion looks
right by every musical argument and the printed page says otherwise: the engraver really did write
a sharp there. Measuring against the editor's corrections alone would have let that rule take
credit for a note it got wrong.

**Ten suggestions survive on the corrected copy**, five of them about slurs the editor was
actively rewriting. Whether those are real findings or noise is not yet checked, and the number is
printed rather than buried precisely so it cannot be quietly ignored.

**The earlier version of this README claimed 85% precision** from the synthetic corpus alone. That
number was real and did not survive contact with real music. It is left here as a marker: any
proofreading tool that quotes a precision figure without telling you what repertoire it was
measured on is quoting you a number about its own test fixtures.

## Status

**Phase 1 is complete and working**: the parser, fifteen musical rules, the confidence layer, the
review application, the correction pipeline, and the evaluation harness. 253 tests.

**Phase 2 (computer vision) is specified and not built.** The interfaces exist and the confidence
layer is already written against them. The hard part is alignment, not classification — finding
which pixels a given note came from is a sub-system in itself (deskew, staff detection,
constrained measure segmentation, monotonic alignment of onsets to notehead positions). Building a
notehead classifier first is the classic way to spend three months and have nothing that works on
a real page.

**Phase 3 (multimodal adjudication) is specified and not built.**

Full plan in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What it finds today

| Rule | What it looks for | Default |
|---|---|---|
| `measure_duration` | Bars whose voices don't fill (or overflow) the time signature | on |
| `tie_integrity` | Dangling ties, and ties between notes of different pitch | on |
| `slur_structure` | Unbalanced slur brackets within a part | on |
| `tuplet_integrity` | Incomplete or inconsistent tuplet groups | on |
| `accidental_consistency` | Sounding pitch disagreeing with the accidentals engraved in its measure | on |
| `carried_accidental` | Long-range accidental carry-over where the key implies a natural | on |
| `enharmonic_spelling` | Notes spelled far from their key when the enharmonic twin sits close | on |
| `motif_deviation` | A repeated figure whose copies disagree at exactly one note | on |
| `harmonic_outlier` | Notes clashing with the chord under them, repairable by one semitone | on |
| `voice_overlap` | Notes in a single voice that sound simultaneously | on |
| `voice_crossing` | Isolated voice crossings in otherwise consistently ordered writing | on |
| `clef_plausibility` | Sustained extreme registers that a different clef would explain | on |
| `key_signature_consistency` | Key signatures contradicted by the accidentals used throughout | on |
| `contour_spike` | Notes breaking an otherwise smooth line, repairable by one step | opt-in |
| `chromatic_outlier` | Isolated non-diatonic notes in a clearly established key | opt-in |

Opt-in rules are switched on by naming them in the config:
`{"analysis": {"detectors": {"contour_spike": {}}}}`.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"        # add ",cv" for PDF scans
```

`.mscz` input and output need a MuseScore executable on `PATH` or in `MUSESCORE_PATH`. We do not
reimplement MuseScore's private format — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §2.7.

## Use

```bash
# Check a score and print what looks wrong
ai-proofreader analyze score.musicxml -v

# Write a shareable report
ai-proofreader analyze score.musicxml -o report.html

# Review it, with the scan side by side
ai-proofreader gui score.musicxml --scan original.pdf

# Apply specific corrections by id, or everything above a confidence
ai-proofreader apply score.musicxml -o corrected.musicxml --accept pit-a1b2c3d4e5
ai-proofreader apply score.musicxml -o corrected.musicxml --accept-above 0.8 --dry-run

# Look at what the parser actually saw
ai-proofreader inspect score.musicxml --measures 12-16
```

In the review window: `A` accept · `R` reject · `I` ignore · `N` next · `P` previous ·
`Shift+N` next pending · `F5` re-analyse · `Ctrl+Z` undo · `Ctrl+S` export.

## How it works

```
scan ─────────────────────────────► cv_engine (Phase 2) ──┐
                                                          ├─► confidence fusion ─► ranked
MuseScore output ─► parser ─► IR ─► music theory ─► rules ─┘                        suggestions
                       │                                                                 │
                       └──── original XML tree ◄──── surgical patch ◄──── accepted ◄──────┘
```

Detectors are pure functions from a shared read-only analysis context to suggestions. They may not
mutate the context, may not call each other, and may not decide whether the user sees their output
— that last decision belongs to the fusion layer. Two rules that would share logic share a
function in `music_theory/` instead, so a change to one can never silently alter another's
behaviour. This is what makes it possible to add a rule without regressing the fourteen already
there, and it is why part-local rules can be sharded across processes for free.

Confidence fusion picks an anchor channel and lets the others add bounded lift, rather than
averaging: two rules agreeing should raise a score, not dilute it. Only evidence that actively
supports the *existing* notation reduces it.

## Contributing

The project is at the stage where an outside contributor can do something that matters in a
weekend. Full guide in [`CONTRIBUTING.md`](CONTRIBUTING.md); places where help would go furthest,
roughly in order:

1. **Real scores with known errors.** More valuable than any code. A scan, the OMR output, and a
   human-corrected version of the same piece is a complete evaluation case, and this repository
   currently has *one*. Repertoire beyond classical piano — chamber parts, choral, guitar
   notation, handwritten manuscript — would immediately show which rules are overfit to one style.
2. **Phase 2 alignment.** Staff detection and measure segmentation on a deskewed page, producing a
   mapping from (part, measure, onset) to a pixel region. This is the single highest-value piece
   of unbuilt work in the project, and it is self-contained.
3. **New rules.** The bar to clear is documented and non-negotiable: a rule must be silent on the
   whole clean corpus, come with negative tests, and be measured on real material before it can be
   on by default. See `src/ai_proofreader/analysis/detectors/spelling.py` for a worked example
   including the false positive it still has.
4. **MuseScore plugin integration**, so suggestions can be reviewed inside MuseScore itself
   instead of in a separate window.
5. **The structural rule nobody has written yet**: OMR engines routinely turn one sustained note
   into tied chord members instead of a separate voice. It happened in five bars of the one real
   score here. It is very common, very mechanical, and completely undetected today.

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — it documents the decisions worth
arguing about, not just the ones that were made.

## Layout

```
src/ai_proofreader/
  models/          the IR: pitches, events, scores, suggestions, edits   (pure pydantic)
  score_parser/    containers, MusicXML → IR, the element handle table
  music_theory/    intervals, keys, harmony, spelling, rhythm, contour   (pure functions)
  analysis/        the shared context, fifteen detectors, motif index
  confidence/      channel protocol, fusion, calibration
  edits/           the applier: surgical patching with exact undo
  pipeline/        orchestration, sharding, reporting
  ui/              PySide6 shell + a Qt-free notation renderer
  testing/         fixture builder, reference corpus, error injection
docs/              architecture, spec, data model, roadmap
scripts/           corpus generation, evaluation, calibration
tests/             253 tests
datasets/          the generated regression corpus
  real/            scan + raw OMR output + human correction + ground truth
```

## Development

```bash
pytest                       # 253 tests
pytest -m slow               # the 100-page performance budget
ruff check src tests scripts
black src tests scripts

python scripts/build_datasets.py                # regenerate the synthetic corpus
python scripts/evaluate.py --min-precision 0.8  # the precision gate
python scripts/evaluate_real.py                 # real OMR output, judged against the scan
python scripts/calibrate.py                     # refit the confidence curve
```

Both gates run in CI. The second one is the one that has actually changed decisions.

Every threshold that decides whether a user sees a suggestion lives in `config.py`, not in a
detector body — an operating point you cannot write down is one you cannot defend.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the system, and the design decisions worth
  arguing about
- [`docs/SPEC.md`](docs/SPEC.md) — what it does, rule by rule, with the numbers
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — the internal representation
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phases 2 to 4, and what would make me change the plan

## Related work

Recognition engines this tool is designed to sit behind, not compete with:
[MuseScore](https://musescore.org) · [Audiveris](https://github.com/Audiveris/audiveris) ·
[homr](https://github.com/liebharc/homr) · [oemer](https://github.com/BreezeWhite/oemer)

If you know of prior work on *post-OMR verification* specifically, please open an issue — the
literature is thin and scattered, and a good survey would help this project more than most code.

## Licence

[AGPL-3.0-or-later](LICENSE). Use it, study it, change it, share it. If you run a modified version
as a network service, publish your changes.

That choice is deliberate: it keeps the project open to contributors while leaving the option of
selling commercial licences to organisations that do not want the copyleft obligation. If you need
different terms, open an issue.

---

Python 3.12 · PySide6 · lxml · NumPy · Pydantic
