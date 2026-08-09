# Technical Specification

## 1. Scope

A post-OMR verification layer for MuseScore Studio. Input: a score MuseScore produced by
recognizing a scan, plus optionally the scan itself. Output: a ranked, explained list of suspected
recognition errors, each with the exact edit that would fix it, and a corrected file once the user
accepts.

Explicitly out of scope: recognizing music from images (that is MuseScore's job), engraving,
playback, and editing beyond the correction vocabulary in [`DATA_MODEL.md`](DATA_MODEL.md) §6.

## 2. The precision contract

The product promise is that a suggestion is worth reading. Concretely:

| Property | Requirement | Enforced by |
|---|---|---|
| Precision at the review threshold | ≥ 80% at 0.60 confidence | `scripts/evaluate.py --min-precision 0.8 --gate-threshold 0.6` |
| False positives on correct music | **zero** per corpus entry | `tests/test_pipeline.py::TestCorpus` |
| Explanation | Every suggestion states its reasoning in one paragraph a musician can check | Review, plus `test_text_report_mentions_every_suggestion` |
| Non-destruction | No file on disk changes without an explicit accept and an explicit export | `test_accepting_does_not_touch_the_file`, `test_apply_defaults_to_changing_nothing` |
| Reversibility | Every applied edit is exactly undoable | `tests/test_edits.py::TestUndo` |

### What the numbers actually say

Measured on the shipped **synthetic** corpus (1019 notes, 57 injected errors), at the 0.60
threshold: 25 reported, 23 correct, **92% precision** and **40% recall**, with nothing at all
reported on the four clean entries.

Read that figure with two qualifications, both of which matter more than the figure:

1. **It uses measure-level matching** — right part, right bar, diagnosis consistent with the
   error. Under exact-onset matching, precision at the same threshold is **56%**. That standard is
   systematically pessimistic (injecting a duration error shifts every later onset in its bar, so
   ground truth recorded against the clean score no longer lines up), which is why the gate uses
   the measure standard — but a 36-point gap is a real weakness, not a rounding difference.
2. **The corpus is synthetic, and synthetic corpora flatter their authors.** Errors are injected
   by the same repository that detects them, into music written by the same repository. An earlier
   revision of this document quoted 85.2% precision from that corpus as though it characterised
   the product; on the first real OMR output the project ever saw, the same configuration returned
   nineteen suggestions of which **zero** were correct, and produced *more* suggestions on the
   human-corrected copy of the score than on the broken one. Two rules were disabled by default as
   a direct result (§4.3, §4.5), and the corpus was demoted from evidence to debugging aid.

The real-material figures are now tracked in the repository and reproduced by
`scripts/evaluate_real.py`, on the one score in the evaluation set that has a scan, an OMR output
and a human correction (`datasets/real/schmitt-op207-2-ii/`):

| | Found | Missed | False positives |
|---|---|---|---|
| Wrong pitches (1) | 0 | 1 | — |
| Wrong spellings (15) | 13 | 2 | 1, verified against the scan |
| Unattributed on the raw file | — | — | 5, not yet checked |
| On the human-corrected copy | — | — | 10 total, 0 spelling |

False positives there are judged against the **scan**, not against the editor's corrections. The
distinction is not pedantic: the one standing false positive (m.22) is a note the editor also left
alone, so scoring against the corrected file would have counted it as correct.

Recall at 0.60 on the synthetic corpus is **40%**; see §7 for why that number is what it is, and
why the missed real pitch error above is the argument for Phase 2 rather than for more rules.

## 3. Inputs and outputs

### Accepted input

| Format | Read | Write | Notes |
|---|---|---|---|
| `.musicxml`, `.xml` | ✅ | ✅ | The interchange format; everything else is converted to it |
| `.mxl` | ✅ | ✅ | Zip container; other entries preserved on write |
| `.mscz`, `.mscx` | ✅ | ✅ | Via the MuseScore executable (`MUSESCORE_PATH` or on `PATH`) |
| `score-timewise` MusicXML | ❌ | ❌ | Rejected with a message pointing at partwise export |

Scans: PDF (needs PyMuPDF), PNG, JPEG, TIFF, BMP, WebP.

### Output

* **Report** — JSON (machine), HTML (shareable, self-contained), text (terminal).
* **Corrected score** — same format as the input by default; a surgical patch of the original, so
  the diff for a one-note fix is one line.

## 4. Musical elements extracted

Notes, rests, chords, voices, staves, measures, accidentals (printed *and* sounding), key
signatures, time signatures (including additive and *senza misura*), ties (sounding and printed),
slurs, tuplets and time modification, articulations, ornaments, technical marks, fermatas, stems,
beams, noteheads, lyrics, clefs (including mid-measure changes and octave clefs), transposition,
dynamics, tempo and metronome marks, wedges, octave shifts, pedals, rehearsal marks, segno/coda,
barlines, repeats and endings, part groups, and score metadata.

Grace notes are parsed and marked; they occupy no musical time and are excluded from rhythmic
checks.

## 5. Detection rules

Fifteen detectors, each independently enableable and weightable through `config.json`. Listed in
registry order — structural first, since a wrong clef invalidates every note-level finding under
it.

Two of them are **off by default**. That is a status, not a bug: they are sound in principle and
false in practice on ordinary tonal repertoire, so they keep their code and their tests and lose
their default. Naming a detector in the config switches it on, even with an empty entry:
`{"analysis": {"detectors": {"contour_spike": {}}}}`.

| Detector | Finds | Typical confidence | Notes |
|---|---|---|---|
| `clef_plausibility` | A whole passage living off the staff | high | Needs ≥ 12 notes averaging > 2.2 ledger lines |
| `key_signature_consistency` | Every F in the part sharpened by hand | high | Needs ≥ 8 measures and ≥ 85% consistency |
| `measure_duration` | Bars that do not add up | very high | Names the fix only when exactly one exists |
| `tuplet_integrity` | Groups that do not sum, brackets that do not close | medium | |
| `tie_integrity` | Ties joining different pitches; dangling ties | **very high** | A tie between different pitches is impossible, not merely odd |
| `slur_structure` | Slurs that open or close alone | low | Often a system break, not an error |
| `voice_overlap` | Two notes at once in one voice | high | Chord split, or a `<backup>` overlap |
| `accidental_consistency` | Sounding pitch contradicting the bar's own accidentals | high | Pure internal consistency; no page needed |
| `enharmonic_spelling` | A note whose sound is right and whose letter is wrong | **high** | The only rule with a real-score measurement; see §6.2 |
| `motif_deviation` | A repeated figure differing at one note | **high** | The flagship rule; see §6 |
| `contour_spike` | A note that leaps away and straight back | medium | **Off by default** — 3 false positives, 0 true, on real material |
| `harmonic_outlier` | A note a semitone or a step from a real chord | medium | Cross-part; works in sounding pitch |
| `carried_accidental` | Long-range carry-over where the key says natural | low | Honest about needing the scan |
| `chromatic_outlier` | A lone foreign pitch in a clearly diatonic passage | low–medium | **Off by default** — 11 false positives, 0 true, on real material |
| `voice_crossing` | A single crossing in otherwise ordered writing | medium | |

### 5.1 Why two rules are switched off

Both fired confidently on the synthetic corpus and both were wrong on the first real score, for
the same underlying reason: they assume a kind of music that the corpus contained and that most
repertoire does not.

`chromatic_outlier` assumes that a non-diatonic note in a clearly established key is suspicious.
The Schmitt sonatina is written in F major and is full of chromatic passing tones and secondary
dominants, which is completely normal for its period. The key estimator, correctly, reports F
major with high confidence, so every chromatic note reads as a lone foreigner: eleven suggestions,
all wrong. The synthetic chromatic étude did not catch this because a *fully* chromatic piece
makes the key estimate unreliable and silences the rule. The dangerous case is the middle one —
tonal music with ordinary chromaticism, which is most music.

`contour_spike` assumes composers write smooth lines and OMR breaks them. Real keyboard writing is
full of deliberate leaps that are smooth in voice-leading terms and rough to an interval-by-
interval measure. It produced three suggestions, all wrong, and missed the one genuine misread
pitch — which was a third, not a spike, and left no roughness to detect.

Neither is deleted. `chromatic_outlier` is right for strictly diatonic repertoire — hymnody, folk
transcription, early counterpoint — and the user who knows that about their material can say so in
one line of config.

### Rules deliberately *not* implemented

* **Stem direction.** MuseScore recomputes stems on layout; a "wrong" stem says nothing about the
  page. Used only as a corroborating detail.
* **General melodic plausibility.** "Is this a good tune" has no threshold that survives contact
  with real repertoire. Every melodic rule here asks the narrower question "is there a one-step
  change that removes a specific anomaly".
* **Automatic correction of ambiguous bars.** When four edits would each fix a measure, the user
  is told the bar is broken and shown the candidates. Picking one would be a coin flip presented
  as a finding.

## 6. Motif matching

The strongest signal available without the scan, because it assumes nothing about style: music
repeats itself, recognition errors do not.

Each window of *L* consecutive melodic notes is described twice, relative to its first note:

* **diatonic shape** — staff-position offsets. A misread notehead changes exactly one entry.
* **chromatic shape** — semitone offsets. A missed accidental changes exactly one entry while
  leaving the diatonic shape identical.

Windows are bucketed by (rhythm, diatonic shape) for accidental deviations, and by (rhythm,
diatonic shape with one position masked) for notehead deviations. Within a bucket, one occurrence
disagreeing with a clear majority at exactly one position is a finding. Three safeguards keep this
honest:

1. The disagreement must be **isolated** — the odd occurrence differs from the majority at exactly
   one position overall, not just at the position being examined.
2. Agreeing occurrences must **not overlap** the odd one. Sliding windows share notes, and
   counting those as independent corroboration manufactures support out of nothing.
3. Diatonic deviations are limited to **±1 staff position**. A difference of a third is far more
   likely to be a compositional variant than a misread notehead.

Complexity O(n·L); the index is built once per run and shared.

### 6.2 Enharmonic spelling

The rule the first real score produced, and the only one in the catalogue with a measurement
against an actual scan rather than against injected errors.

The error class it addresses is invisible to every other rule, because nothing about it is wrong
acoustically: the engine hears the right sound and writes the wrong letter. C-sharp where the page
says D-flat sounds identical on playback and is unambiguously wrong to a reader. Fifteen of the
seventeen errors in the reference score were of this kind.

Three signals decide it, in strength order:

1. **Resolution.** An altered note rising a semitone is spelled sharp; one falling a semitone is
   spelled flat. Agreement is a *veto* — a rising sharp is correct however far the key sits — and
   disagreement is the strongest available evidence, so it earns the lowest threshold.
2. **Distance from the key** on the circle of fifths, where position = `step_fifths + 7 × alter`.
   In F major (−1), D-flat sits 4 steps away and C-sharp sits 8.
3. **The vertical spelling.** Notes engraved together lie in a compact window of the line of
   fifths — G-flat/B-flat/D-flat spans four, the same sound written F-sharp/B-flat/D-flat spans
   eleven. A respelling that pulls a wide chord together is corroboration from the harmony rather
   than from the key.

Signal 2 is asymmetric and must be handled explicitly: the seven natural letters occupy positions
−1 to 5, so a sharp *always* measures further from the key number than its flat twin, in every
key. Read literally the measure would flatten every sharp in C major. It is therefore only allowed
to propose accidentals of the kind the key already uses — flats in flat keys, sharps in sharp
keys, nothing at all in C major without corroboration from signal 1 or 3.

Measured against the reference scan: 13 of 15 found, one false positive (m.22, where the engraver
really did print a sharp in a diminished seventh that resolves like a flat), and **nothing** on
the human-corrected copy of the same piece.

## 7. Confidence

Four channels: `musical`, `pattern`, `visual` (Phase 2), `ai` (Phase 3). Fusion, in order:

1. **Within a channel**, supporting evidence combines with a noisy-OR; contrary evidence subtracts.
2. **Across channels**, the best-supported channel anchors the score and the others add a bounded
   lift (`corroboration_gain`, default 0.5 of the remaining headroom). Averaging would mean a
   second rule *agreeing* made us less sure.
3. **Contradiction** — a channel reporting that the page shows the existing notation — multiplies
   the score down by `contradiction × disagreement_penalty` (default 0.9). It is the only thing
   that can reduce a score, and it is close to decisive, because it is the channel that looked.
4. **Ceiling.** With no visual channel present, the result is capped at `musical_only_ceiling`
   (default 0.85). Theory alone should not claim near-certainty about a page it has not seen.
5. **Calibration.** A logistic maps the fused score to a displayed probability. Parameters live in
   config and are fitted by `scripts/calibrate.py` against the corpus, which also prints a
   reliability table. The shipped defaults are deliberately conservative: the corpus is currently
   41 samples, too few to justify a sharp curve, and the script says so.

Why recall is 40% and not 80%: the errors the musical layer misses are the ones with no musical
signature at all — a notehead moved one step in a non-repeating inner voice that stays diatonic
and stays consonant is *musically indistinguishable* from what the composer might have written.
Nothing short of looking at the page finds it. That is Phase 2, and it is why the visual channel
carries the highest fusion weight.

## 8. Performance

Budget: 100-page score, initial musical analysis under 30 seconds.

| Stage | Measured (16 parts × 400 bars, 24 000 notes, 4 cores) |
|---|---|
| XML load | 0.2 s |
| Parse to IR | 2.7 s |
| Context indexing | 2.2 s |
| Detectors (sharded) | ~1 s wall |
| **Total** | **5.0 s** (6.8 s serial) |

Rules that keep this true: no detector worse than O(n log n); every shared index built once in
`AnalysisContext`; nothing re-walks the XML after parse; part shards are sliced in the parent so a
worker receives only its own part. `tests/test_performance.py` asserts the budget and that
sharding beats serial.

CV and AI verification are explicitly outside this budget — they run as an incremental second
pass that streams into an already-usable list.

## 9. Failure posture

* A malformed measure is recorded as a `ParseIssue` and skipped; the rest of the score is still
  analysed. Scores out of an OMR engine are exactly the scores most likely to be malformed.
* A detector that raises is logged and skipped. One bad rule cannot cost the other thirteen.
* A verifier that raises is skipped; fusion renormalizes without it.
* Parallel analysis that cannot start falls back to serial rather than failing.
* An edit that cannot be applied leaves the document untouched and reports why; a multi-edit
  suggestion that fails partway is rolled back.

## 10. User interface

Three panels: scan (left), recognized notation (centre), suggestion queue (right).

* **Keyboard first.** `A` accept · `R` reject · `I` ignore · `N` next · `P` previous ·
  `Shift+N` next pending · `F5` re-analyse · `Ctrl+Z` undo · `Ctrl+S` export.
* **Analysis runs off the UI thread.**
* **Accepting applies to the in-memory document only.** The notation panel re-renders so the user
  sees the result; the file on disk is untouched until an explicit export, and quitting with
  unexported corrections asks first.
* Low-confidence suggestions are hidden behind a toggle rather than deleted.
* Notation is rendered by a pure layout engine into SVG, so the picture in the window, in an HTML
  report and in the documentation are the same picture, and layout bugs are caught by pytest.

## 11. Testing

| Suite | Covers |
|---|---|
| `test_models` | Pitch coordinates, key/clef/time semantics, transposition, exact rationals |
| `test_parser` | Chords, backup/forward, tuplets, ties, sticky attributes, containers, malformed input |
| `test_music_theory` | Interval spelling, key estimation, chord identification, accidental carry-over, contour |
| `test_detectors` | Each rule's positive case *and* the ordinary music it must ignore |
| `test_edits` | Surgical patching, schema ordering, undo, layout preservation |
| `test_confidence` | Renormalization, corroboration, contradiction, calibration, merging |
| `test_pipeline` | End-to-end analysis, apply, reports, CLI, corpus behaviour |
| `test_ui` | Notation layout geometry, SVG output, and the real accept/reject/undo flow offscreen |
| `test_performance` | The 30-second budget and sharding equivalence (marked `slow`) |

Plus `scripts/evaluate.py` as a standing regression gate on precision.
