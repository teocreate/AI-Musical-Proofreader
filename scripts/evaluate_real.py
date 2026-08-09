#!/usr/bin/env python3
"""Score the detectors against real OMR output, not injected errors.

    python scripts/evaluate_real.py

The synthetic corpus in ``datasets/`` measures whether the rules can find the mistakes this
repository knows how to make. This script measures something harder and more useful: whether they
find the mistakes a real OMR engine actually made on a real printed page. Those turned out to be
different problems — see ``datasets/real/*/README.md`` — and every precision claim in the project
README that is worth anything comes from here.

Three numbers are reported per case, and the third is the one to watch:

* **recall** on the errors the human fixed, by kind;
* **false positives on the raw file**, judged against the scan rather than against the human's
  corrections, so that a rule cannot get credit for "finding" something the page says is correct;
* **suggestions on the corrected file**, which should be zero. A proofreader that talks on clean
  music is a proofreader people switch off, and this is the only place that gets measured against
  music a person actually checked.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_proofreader.config import DEFAULT_CONFIG, ProofreaderConfig  # noqa: E402
from ai_proofreader.models import Suggestion  # noqa: E402
from ai_proofreader.pipeline import Proofreader  # noqa: E402

DEFAULT_ROOT = REPO_ROOT / "datasets" / "real"


@dataclass(frozen=True)
class Slot:
    """Where a suggestion or a known error sits, as coarsely as both can agree on."""

    part: str
    measure: int
    staff: int
    onset: str

    @classmethod
    def of_suggestion(cls, suggestion: Suggestion) -> Slot:
        target = suggestion.target
        return cls(target.part_id, int(target.measure_number), target.staff, str(target.onset))

    @classmethod
    def of_entry(cls, entry: dict) -> Slot:
        return cls(entry["part"], int(entry["measure"]), int(entry["staff"]), str(entry["onset"]))


@dataclass
class CaseResult:
    name: str
    found: list[dict] = field(default_factory=list)
    missed: list[dict] = field(default_factory=list)
    false_positives: list[Suggestion] = field(default_factory=list)
    known_false_positives: list[Suggestion] = field(default_factory=list)
    unattributed: list[Suggestion] = field(default_factory=list)
    on_corrected: list[Suggestion] = field(default_factory=list)


def load_cases(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/ground_truth.json"))


def evaluate(case: Path, config: ProofreaderConfig) -> CaseResult:
    truth = json.loads((case / "ground_truth.json").read_text(encoding="utf-8"))
    result = CaseResult(name=truth.get("name", case.name))

    proofreader = Proofreader(config)
    raw = proofreader.analyze_file(case / truth["raw"]).report.suggestions
    result.on_corrected = list(
        proofreader.analyze_file(case / truth["corrected"]).report.suggestions
    )

    by_slot: dict[Slot, list[Suggestion]] = {}
    for suggestion in raw:
        by_slot.setdefault(Slot.of_suggestion(suggestion), []).append(suggestion)

    matched: set[int] = set()
    for entry in truth["errors"]:
        slot = Slot.of_entry(entry)
        hit = next(
            (s for s in by_slot.get(slot, []) if s.suggested_repr == entry["should"]),
            None,
        )
        if hit is None:
            result.missed.append(entry)
            continue
        matched.add(id(hit))
        result.found.append(
            entry | {"detector": hit.detector, "confidence": hit.confidence.percent}
        )

    # A suggestion that lands where the *scan* says the file is already right is a false positive
    # however plausible its reasoning was. Judging only against the human's edits would hide these.
    known_good = {Slot.of_entry(item) for item in truth.get("verified_correct", [])}
    for suggestion in raw:
        if id(suggestion) in matched:
            continue
        if Slot.of_suggestion(suggestion) in known_good:
            result.known_false_positives.append(suggestion)
        else:
            result.unattributed.append(suggestion)
    result.false_positives = result.known_false_positives
    return result


def report(result: CaseResult) -> None:
    print(f"\n=== {result.name} ===")

    kinds = sorted({entry["kind"] for entry in result.found + result.missed})
    print("\n  recall, by error kind")
    print("  " + "-" * 48)
    for kind in kinds:
        found = sum(1 for e in result.found if e["kind"] == kind)
        total = found + sum(1 for e in result.missed if e["kind"] == kind)
        share = f"{found / total:6.0%}" if total else "     —"
        print(f"    {kind:<12} {found:3d}/{total:<3d} {share}")

    if result.missed:
        print("\n  missed")
        for entry in result.missed:
            print(
                f"    m.{entry['measure']:<3} staff {entry['staff']}  "
                f"{entry['was']} → {entry['should']}  ({entry['kind']})"
            )

    print(f"\n  false positives against the scan: {len(result.false_positives)}")
    for suggestion in result.false_positives:
        target = suggestion.target
        print(
            f"    m.{target.measure_number:<3} staff {target.staff}  "
            f"{suggestion.current_repr} → {suggestion.suggested_repr}  [{suggestion.detector}]"
        )

    if result.unattributed:
        print(
            f"\n  unattributed suggestions: {len(result.unattributed)}"
            "  (neither a known error nor a known-correct note — verify against the scan"
            " and move each into ground_truth.json)"
        )
        for detector, count in Counter(s.detector for s in result.unattributed).most_common():
            print(f"    {count:3d}  {detector}")

    print(f"\n  suggestions on the human-corrected file: {len(result.on_corrected)}  (target: 0)")
    for detector, count in Counter(s.detector for s in result.on_corrected).most_common():
        print(f"    {count:3d}  {detector}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--max-false-positives",
        type=int,
        default=None,
        help="Exit non-zero if scan-verified false positives exceed this. For CI once the set is "
        "big enough that the number means something.",
    )
    args = parser.parse_args(argv)

    config = ProofreaderConfig.load(args.config) if args.config else DEFAULT_CONFIG
    cases = load_cases(args.root)
    if not cases:
        print(f"No evaluation cases under {args.root}.", file=sys.stderr)
        print(
            "Real cases are contributed, not generated — see the Score donation issue template.",
            file=sys.stderr,
        )
        return 1

    total_fp = 0
    for case in cases:
        result = evaluate(case, config)
        report(result)
        total_fp += len(result.false_positives)

    if args.max_false_positives is not None and total_fp > args.max_false_positives:
        print(
            f"\nFAIL: {total_fp} false positives, limit {args.max_false_positives}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
