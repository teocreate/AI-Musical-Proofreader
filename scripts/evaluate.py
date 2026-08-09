#!/usr/bin/env python3
"""Score the detectors against the regression corpus.

Prints a precision/recall curve over confidence thresholds, per-error-kind recall, and the
per-detector false-positive count. This is the number the product promise rests on, so it is a
script anyone can run rather than a claim in a README:

    python scripts/build_datasets.py
    python scripts/evaluate.py

Two matching standards are reported, because they answer different questions:

**measure** — same part, same measure, and a suggestion kind consistent with the injected error.
This is the user-facing standard: pointing a proofreader at the right bar with the right
diagnosis is the deliverable, and it is what actually saves them time.

**onset** — additionally requires the exact onset. Stricter, but systematically pessimistic:
injecting a duration error shifts every later onset in that bar, so ground truth recorded against
the clean score no longer lines up with the corrupted one. Reported for completeness; the gate
uses the measure standard.

Under both, landing on the right note with the wrong diagnosis is *not* a hit — telling a user
"this bar does not add up" when the real problem is a missing sharp wastes their time in a subtler
way than saying nothing.

False positives are counted everywhere, including the negative-control entry, which contains no
errors at all and therefore measures the cost of every rule directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_proofreader.config import DEFAULT_CONFIG, ProofreaderConfig  # noqa: E402
from ai_proofreader.models import Suggestion  # noqa: E402
from ai_proofreader.pipeline import Proofreader  # noqa: E402
from ai_proofreader.testing.corruption import InjectedError  # noqa: E402

THRESHOLDS = (0.35, 0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass
class Match:
    suggestion: Suggestion
    error: InjectedError | None
    exact_onset: bool = False

    @property
    def is_hit(self) -> bool:
        return self.error is not None


def match_predictions(
    suggestions: list[Suggestion], errors: list[InjectedError], strict: bool = False
) -> tuple[list[Match], list[InjectedError]]:
    """Greedy one-to-one matching of suggestions to injected errors.

    Highest-confidence suggestions are matched first, so a correct high-confidence finding is
    never displaced by a lower-confidence one that happens to sit in the same bar.
    """
    remaining = list(errors)
    matches: list[Match] = []
    for suggestion in sorted(suggestions, key=lambda item: -item.confidence.calibrated):
        found: InjectedError | None = None
        exact = False
        for error in remaining:
            if (
                error.part_id != suggestion.target.part_id
                or error.measure_index != suggestion.target.measure_index
            ):
                continue
            if suggestion.kind not in error.expected_kinds:
                continue
            onset_matches = str(error.onset) == str(suggestion.target.onset)
            if strict and not onset_matches:
                continue
            found = error
            exact = onset_matches
            break
        if found is not None:
            remaining.remove(found)
        matches.append(Match(suggestion=suggestion, error=found, exact_onset=exact))
    return matches, remaining


def evaluate(dataset: Path, config: ProofreaderConfig) -> dict[str, object]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    proofreader = Proofreader(config)

    all_matches: list[Match] = []
    strict_matches: list[Match] = []
    missed: list[InjectedError] = []
    injected_by_kind: Counter[str] = Counter()
    hit_by_kind: Counter[str] = Counter()
    false_positive_detectors: Counter[str] = Counter()
    total_notes = 0
    total_ms = 0.0

    for entry in manifest["entries"]:
        path = dataset / (entry["corrupted"] or entry["clean"])
        errors = [InjectedError.from_dict(item) for item in entry["errors"]]
        for error in errors:
            injected_by_kind[error.kind.value] += 1

        result = proofreader.analyze_file(path)
        total_notes += result.report.note_count
        total_ms += result.report.total_ms

        matches, unmatched = match_predictions(list(result.report.suggestions), errors)
        all_matches.extend(matches)
        strict_matches.extend(
            match_predictions(list(result.report.suggestions), errors, strict=True)[0]
        )
        missed.extend(unmatched)
        for match in matches:
            if match.is_hit and match.error is not None:
                hit_by_kind[match.error.kind.value] += 1
            else:
                for name in match.suggestion.detector.split("+"):
                    false_positive_detectors[name] += 1

    total_injected = max(1, sum(injected_by_kind.values()))

    def build_curve(matches: list[Match]) -> list[dict[str, float | int]]:
        rows: list[dict[str, float | int]] = []
        for threshold in THRESHOLDS:
            kept = [
                match for match in matches if match.suggestion.confidence.calibrated >= threshold
            ]
            hits = sum(1 for match in kept if match.is_hit)
            rows.append(
                {
                    "threshold": threshold,
                    "reported": len(kept),
                    "true_positives": hits,
                    "false_positives": len(kept) - hits,
                    "precision": hits / len(kept) if kept else 1.0,
                    "recall": hits / total_injected,
                }
            )
        return rows

    return {
        "curve": build_curve(all_matches),
        "curve_exact_onset": build_curve(strict_matches),
        "injected_by_kind": dict(injected_by_kind),
        "hit_by_kind": dict(hit_by_kind),
        "false_positive_detectors": dict(false_positive_detectors),
        "missed": [error.to_dict() for error in missed],
        "total_notes": total_notes,
        "total_ms": total_ms,
    }


def print_report(results: dict[str, object]) -> None:
    print(f"\nCorpus: {results['total_notes']} notes analysed in {results['total_ms']:.0f} ms")
    for label, key in (("measure", "curve"), ("exact onset", "curve_exact_onset")):
        print(f"\n  Matching standard: {label}")
        print("  threshold  reported   TP   FP  precision  recall")
        print("  " + "-" * 50)
        for row in results[key]:  # type: ignore[union-attr]
            print(
                f"  {row['threshold']:>9.2f}  {row['reported']:>8}  {row['true_positives']:>3}  "
                f"{row['false_positives']:>3}  {row['precision']:>9.1%}  {row['recall']:>6.1%}"
            )

    injected: dict[str, int] = results["injected_by_kind"]  # type: ignore[assignment]
    hits: dict[str, int] = results["hit_by_kind"]  # type: ignore[assignment]
    if injected:
        print("\n  Recall by injected error kind (at the reporting threshold):")
        for kind, count in sorted(injected.items(), key=lambda item: -item[1]):
            found = hits.get(kind, 0)
            print(f"    {kind:<22} {found:>3}/{count:<3} {found / count:>6.0%}")

    detectors: dict[str, int] = results["false_positive_detectors"]  # type: ignore[assignment]
    if detectors:
        print("\n  False positives by detector:")
        for name, count in sorted(detectors.items(), key=lambda item: -item[1]):
            print(f"    {name:<32} {count}")

    missed: list[dict[str, object]] = results["missed"]  # type: ignore[assignment]
    if missed:
        print(f"\n  Missed ({len(missed)}):")
        by_kind: dict[str, list[str]] = defaultdict(list)
        for error in missed:
            by_kind[str(error["kind"])].append(
                f"{error['part_id']} m.{int(error['measure_index']) + 1} "
                f"{error['before']}→{error['after']}"
            )
        for kind, items in sorted(by_kind.items()):
            print(f"    {kind}: {', '.join(items[:6])}{' …' if len(items) > 6 else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "datasets")
    parser.add_argument("--config", type=Path, help="JSON configuration to evaluate")
    parser.add_argument("--json", type=Path, help="write full results as JSON")
    parser.add_argument(
        "--min-precision",
        type=float,
        help="exit non-zero if precision at --gate-threshold falls below this (for CI)",
    )
    parser.add_argument("--gate-threshold", type=float, default=0.7)
    args = parser.parse_args()

    if not (args.dataset / "manifest.json").is_file():
        print(f"No corpus at {args.dataset}. Run scripts/build_datasets.py first.", file=sys.stderr)
        return 2

    config = ProofreaderConfig.load(args.config) if args.config else DEFAULT_CONFIG
    results = evaluate(args.dataset, config)
    print_report(results)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nFull results written to {args.json}")

    if args.min_precision is not None:
        row = min(
            results["curve"],  # type: ignore[arg-type]
            key=lambda item: abs(item["threshold"] - args.gate_threshold),
        )
        if row["precision"] < args.min_precision:
            print(
                f"\nFAIL: precision {row['precision']:.1%} at threshold "
                f"{row['threshold']:.2f} is below the required {args.min_precision:.1%}",
                file=sys.stderr,
            )
            return 1
        print(f"\nPASS: precision {row['precision']:.1%} at threshold {row['threshold']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
