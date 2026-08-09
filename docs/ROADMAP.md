# Roadmap

Four phases. Each one ships something a user can run, and each one is finished — tested,
measured, documented — before the next begins. The ordering is not arbitrary: every later phase
depends on the measurement harness built in Phase 1, because without it there is no way to know
whether a new signal helped or just added noise.

---

## Phase 1 — Musical analysis, review UI, correction pipeline ✅ **complete**

Everything that can be known from the file alone.

**Shipped**

- MusicXML / MXL / MSCZ loading, with the handle table that makes corrections surgical
- Full parser: chords, voices, backup/forward, tuplets, ties, sticky and mid-measure attributes,
  transposition, directions, barlines, repeats
- Music-theory layer: spelled intervals, Krumhansl–Schmuckler key estimation, chord identification,
  accidental carry-over semantics, contour analysis, notatable durations
- Fourteen detectors across rhythm, ties, voices, accidentals, pitch, motif and structure
- Motif index with approximate matching — the flagship rule
- Confidence fusion with channel renormalization, corroboration and contradiction, plus a fitted
  calibration
- Surgical edit applier with exact undo and schema-correct insertion
- Three-panel PySide6 review window with a keyboard-first workflow
- Pure notation renderer (layout → SVG) shared by the window, reports and docs
- CLI: `analyze`, `apply`, `inspect`, `gui`
- Regression corpus with realistic error injection, an evaluation harness, and a calibration fitter
- 244 tests; measured 85% precision at the 0.60 threshold

**Deliberately deferred**: anything requiring the scan.

---

## Phase 2 — Computer-vision verification

The phase that raises recall, and the one with the real technical risk. The risk is **not** symbol
classification — it is localization. MuseScore's MusicXML carries no reliable mapping back to
scan pixels, so finding the notehead a suggestion refers to is a sub-system, not a function call.

**2.1 Page preparation**
- Deskew by projection-profile maximization; rasterize PDFs at 200–300 dpi
- Staff-line detection by horizontal run-length analysis; estimate `staff_space` per system
- Staff-line removal for symbol work, keeping an unremoved copy for tie and slur detection

**2.2 Alignment — the hard part**
- Page → staff systems (projection profile), system → staves, staff → measures
- Measure segmentation is *constrained*, not free: the score tells us how many measures a system
  should contain, turning detection into assignment
- Measure → element by monotonic DTW between the measure's onset sequence and detected notehead
  x-centroids, producing an anchor **with a search radius**, not a point
- Every stage emits a confidence; a low-confidence alignment abstains rather than guessing

**2.3 Local verification**
- Notehead centroid → staff position, compared against the file's reading
- Accidental presence and identity in the notehead's left neighbourhood
- Augmentation dot presence to the right
- Tie/slur arc detection between two anchors
- Stem and flag counting for duration checks

**2.4 Integration**
- `SourceRegion` populated on suggestion targets; the scan panel's highlight overlay activates
- A `Verifier` implementation feeding the `visual` channel — the interface already exists and the
  fusion layer is already written against it
- Runs as an incremental second pass so the musical findings stay immediately usable

**Exit criteria**: recall at the 0.60 threshold rises above 65% on the corpus **without** precision
falling below 85%; a suggestion contradicted by the page is demonstrably suppressed.

**New risk to watch**: historical engravings with broken staff lines and heavy bleed-through. The
corpus needs real scanned material before this phase can claim to be done, which means acquiring
public-domain scans and hand-labelling them — the single largest non-code cost in the plan.

---

## Phase 3 — Multimodal adjudication

A vision model as a **tie-breaker**, not a scanner. Gating rule: a region reaches the model only
when musical analysis flagged it *and* CV was ambiguous or disagreed. That is a few dozen calls
per score rather than tens of thousands, which is the difference between a viable product and an
unshippable API bill.

- Prompt construction: image crop with context, the current interpretation, the proposed
  alternative, and the surrounding musical context — the model is asked to *choose*, not to read
- Response parsing into `Evidence` with an explanation the user sees verbatim
- Content-hash caching so re-analysing a score costs nothing
- Batching and a hard per-score call budget
- Feeds the `ai` channel; abstains cleanly when unavailable, offline or over budget

**Exit criteria**: on the subset of suggestions the model adjudicates, precision improves
measurably against the labelled corpus, and disabling it changes nothing else.

---

## Phase 4 — Integration, scale and polish

- **MuseScore plugin** — open the corrected file, or drive proofreading from inside MuseScore
- **Batch mode** — proofread a library, emit one report per score, CI-friendly exit codes
- **Bravura/SMuFL rendering** behind the existing glyph interface, with the drawn glyphs as the
  fallback when the font is absent
- **Beamed notation** in the review panel
- **Learned scoring** — replace hand-tuned detector weights with a model fitted on accumulated
  user accept/reject decisions, which by then is the most valuable asset the product has
- **Incremental re-analysis** — re-check only the measures a correction touched instead of the
  whole score
- Installers for macOS, Windows and Linux

---

## What would make me change this plan

Written down now, while it is still cheap to be wrong:

* **If CV localization proves unreliable on real scans**, Phase 2 inverts: instead of locating the
  file's notes on the page, run a lightweight second recognizer over the page and *diff* the two
  readings. Slower and cruder, but it degrades gracefully where alignment does not.
* **If the vision model turns out to be good at whole systems rather than crops**, Phase 3's
  gating changes from per-suggestion to per-system, and the cost model changes with it.
* **If users mostly reject one detector's findings**, that is data, not an opinion — the accept
  and reject decisions are the training signal Phase 4 is built on, and a rule the user always
  rejects should be weighted down automatically rather than defended.
