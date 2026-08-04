# AI Musical Proofreader

Intelligent post-OMR correction for MuseScore Studio.

MuseScore has already recognized your scan. This tool reads the result, treats it as a
*hypothesis*, and tells you which notes it does not believe — with a reason you can check in five
seconds and a one-click fix.

It is a spell checker for notation. It never changes your score on its own.

```
[ 85%] high     Flute m. 5 (staff 1, voice 1)
    Repeated figure differs by 1 semitone
    F5 -> F#5
    The same figure — same staff positions, same rhythm — appears at m. 1, m. 3, m. 7 with
    F#5 where this copy has F5. The notehead sits on the same line in every copy, so the
    difference is an accidental, and a missed accidental is far likelier than a deliberate
    one-note variant.
```

## Status

**Phase 1 is complete**: musical analysis, the review application, and the correction pipeline.
Computer-vision verification (Phase 2) and multimodal adjudication (Phase 3) are specified in
[`docs/ROADMAP.md`](docs/ROADMAP.md) and not yet built — the interfaces they plug into exist and
the confidence layer is already written against them.

What that means in practice: the tool currently finds errors that leave a trace *in the music* —
repeated figures that disagree, bars that do not add up, ties joining different pitches,
accidentals contradicting their own measure. It cannot yet find a misread notehead that happens to
be musically plausible. That needs the page, and the page is Phase 2.

### Measured, not asserted

On the shipped regression corpus (`scripts/build_datasets.py` → `scripts/evaluate.py`):

| Confidence threshold | Reported | Correct | Precision | Recall |
|---|---|---|---|---|
| 0.50 | 35 | 26 | 74% | 46% |
| **0.60** | **27** | **23** | **85%** | **40%** |
| 0.70 | 7 | 6 | 86% | 11% |
| 0.80 | 4 | 4 | 100% | 7% |

One false positive across 290 notes of correct music, and none above the review threshold. Recall
is the number Phase 2 exists to raise; precision is the number that must never move.

Run it yourself — the corpus generator, the evaluation harness and the calibration fitter all ship
in `scripts/`.

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

Four decisions shape everything else:

**Corrections are patches, not re-exports.** The original XML tree stays alive and every note in
the internal model holds a handle back to its element. Accepting a pitch fix produces a one-line
diff; your page layout, credits and engraving hints come back untouched. Round-tripping through an
internal model would silently destroy all of it.

**Precision is the product.** A proofreader that flags 400 things on a 30-page score has made your
job worse. Rules that cannot tell you *which* note is wrong say so instead of guessing, and the
Accept button is disabled for them. The negative-control entry in the corpus — a chromatic étude
with nothing wrong in it — exists specifically to measure what each rule costs.

**Confidence means something.** Channels that did not run are renormalized away rather than
counted as zero; a rule agreeing with another raises the score instead of averaging it down;
evidence that the page supports the *existing* notation collapses it. Without a visual channel the
whole system is capped at 85%, because theory alone should not claim certainty about a page it has
not seen.

**The hard part of Phase 2 is alignment, not classification.** Finding which pixels a given note
came from is a sub-system — deskew, staff detection, constrained measure segmentation, then
monotonic alignment of onsets to notehead positions. Building a notehead classifier first is the
classic way to burn a quarter and have nothing that works on a real page.

## Layout

```
src/ai_proofreader/
  models/          the IR: pitches, events, scores, suggestions, edits   (pure pydantic)
  score_parser/    containers, MusicXML → IR, the element handle table
  music_theory/    intervals, keys, harmony, spelling, rhythm, contour   (pure functions)
  analysis/        the shared context, fourteen detectors, motif index
  confidence/      channel protocol, fusion, calibration
  edits/           the applier: surgical patching with exact undo
  pipeline/        orchestration, sharding, reporting
  ui/              PySide6 shell + a Qt-free notation renderer
  testing/         fixture builder, reference corpus, error injection
docs/              architecture, spec, data model, roadmap
scripts/           corpus generation, evaluation, calibration
tests/             244 tests
datasets/          the generated regression corpus
```

## Development

```bash
pytest                       # 244 tests
pytest -m slow               # the 100-page performance budget
ruff check src tests scripts
black src tests scripts

python scripts/build_datasets.py                # regenerate the corpus
python scripts/evaluate.py --min-precision 0.8  # the precision gate
python scripts/calibrate.py                     # refit the confidence curve
```

Every threshold that decides whether a user sees a suggestion lives in `config.py`, not in a
detector body — an operating point you cannot write down is one you cannot defend.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the system, and the design decisions worth
  arguing about
- [`docs/SPEC.md`](docs/SPEC.md) — what it does, rule by rule, with the numbers
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — the internal representation
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phases 2 to 4, and what would make me change the plan
