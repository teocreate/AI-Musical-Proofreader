# Schmitt, Sonatina op. 207 no. 2, movement II

The first real evaluation case in this repository, and for now the only one.

| File | What it is |
|---|---|
| `scan.pdf` | The original printed page. The arbiter: where this and a human disagree, this wins. |
| `raw.musicxml` | What MuseScore 4 produced from that scan, untouched. |
| `corrected.musicxml` | A musician's correction of the raw file, made while reading the scan. |
| `ground_truth.json` | The errors, machine-readable, with the provenance of each. |

Jacob Schmitt died in 1853; the work and this edition are in the public domain, and the score was
contributed by the repository owner specifically so these numbers can be reproduced.

## Why one real score is worth more than the whole synthetic corpus

The synthetic corpus injects errors this repository knows how to make, into music this repository
wrote. It is a debugging tool. This file is what an OMR engine actually does to actual printed
music, and it disagreed with the synthetic corpus on nearly every point that mattered:

* **The error mix is nothing like the injected one.** One wrong pitch, fifteen wrong *spellings*,
  and roughly twenty structural slots where a sustained note became tied chord members. The
  corpus injects notehead shifts, halved durations and stray accidentals — which is to say, the
  errors are audible, and almost all the real ones are not.
* **Two detectors that looked good were wrong here.** `chromatic_outlier` produced eleven false
  positives and `contour_spike` three, with zero true positives between them. Both are now off by
  default; `docs/SPEC.md` §5.1 explains why.
* **The one audible error was invisible to every rule.** See the `note` field on it in
  `ground_truth.json`. It is the clearest argument in the project for building the vision channel.

## Provenance of each entry

`source` says how far a ground-truth entry has been checked:

* `scan-verified` — the printed page was rendered and read at the relevant bar. Four entries carry
  this: the pitch error (m.6), one spelling (m.14), and both `verified_correct` entries.
* `human-correction` — taken from the difference between `raw` and `corrected`. The editor was
  reading the scan, and the whole passage is unambiguously in the flat region, so these are
  reliable; they are simply not *individually* re-checked against the page.

The `verified_correct` list matters as much as the errors: it records places where the tool
proposes a change and the printed page says the file is right. Measuring against the human's
corrections alone would let a rule quietly get credit for those.

## Reproducing the numbers

```bash
python scripts/evaluate_real.py
```

Reported in the README as: 13 of 15 spellings found, one false positive (m.22), zero suggestions
on the corrected copy, and 0 of 1 wrong pitch.

## Contributing another

One score is one repertoire, one engine, one editor's habits. If you have a scan, its raw OMR
output and a corrected version — especially of anything that is not nineteenth-century piano —
open a `Score donation` issue. The raw output is the part people usually throw away and the part
that makes the case usable.
