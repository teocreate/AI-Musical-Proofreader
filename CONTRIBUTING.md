# Contributing

This project is small enough that a first contribution can matter, and opinionated enough that
it is worth reading this page before writing code.

## The one rule

**A new detector must be silent on the entire clean corpus before it is on by default.**

`tests/test_pipeline.py::TestCorpus` enforces exactly that: four correct scores, zero suggestions.
There is no allowance and the allowance was deliberately removed, because it was the slot a new
rule's first false positive would quietly fill.

If your rule is right in principle but noisy in practice, it still ships — set
`enabled_by_default = False` on the class, document *why* in the class docstring, and let users
opt in. Two rules live in the tree on those terms today (`chromatic_outlier`, `contour_spike`).
That is a respectable outcome, not a rejection.

## Where help goes furthest

| # | Area | Why it matters | Difficulty |
|---|---|---|---|
| 1 | **Real scores with known errors** | A scan + OMR output + human-corrected version is a complete evaluation case. The repository has *one*. | No code needed |
| 2 | **Phase 2 alignment** | Deskew, staff detection, measure segmentation, then map (part, measure, onset) → pixel region. Self-contained, highest value, entirely unbuilt. | Hard |
| 3 | **The tied-chord-member rule** | OMR engines routinely turn one sustained note into tied chord members instead of a separate voice. It happened in five bars of the one real score here, and nothing detects it. | Medium |
| 4 | **MuseScore plugin integration** | Review suggestions inside MuseScore instead of a separate window. | Medium |
| 5 | **Repertoire breadth** | Every rule here was tuned on classical piano. Choral, guitar, chamber parts and handwritten manuscript will each break something, and finding out which is progress. | Easy |

Item 1 is genuinely the bottleneck. If you have digitised anything and kept both versions, that
is more useful to this project than a pull request.

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
pytest
```

## Adding a detector

1. Write it in `src/ai_proofreader/analysis/detectors/`. Subclass `Detector`, set `name`,
   `description`, `kinds`, `channel` and `defaults`, implement `run()`.
2. Every threshold goes in `defaults`, never inline in the body. `scripts/evaluate.py` sweeps
   those values, and a number hidden in a function makes the sweep lie.
3. Register it in `detectors/__init__.py` and `analysis/registry.py`.
4. Write tests in `tests/test_detectors.py` — **the error it finds, and the ordinary music it must
   stay silent about**. The second kind is the one that matters.
5. Run `python scripts/evaluate.py` and put the numbers in the pull request.

The contract for `run()` is narrow on purpose: pure function of the shared context, no mutation,
no calling other detectors, no deciding whether the user sees the output. That last decision
belongs to the fusion layer. Two rules that want to share logic share a function in
`music_theory/` instead — so a change to one can never silently alter another's behaviour, and
part-local rules can be sharded across processes for free.

`src/ai_proofreader/analysis/detectors/spelling.py` is the worked example. It documents its own
false positive.

## Style

```bash
ruff check src tests scripts
black src tests scripts
```

Type hints everywhere. Comments explain *why*, not *what* — the code already says what.
Explanations shown to users are written for musicians, not developers: "3 other occurrences of
this figure read F#5" is checkable in five seconds; "confidence 0.87" is not.

## Pull requests

Say what you measured. A rule change without corpus numbers is not reviewable, and a rule change
measured only on synthetic data is exactly how the two disabled detectors got shipped in the first
place.

## Licence

Contributions are accepted under the AGPL-3.0-or-later, the same licence as the project.
