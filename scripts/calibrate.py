#!/usr/bin/env python3
"""Fit the confidence calibration against the regression corpus.

The displayed percentage is a claim about how often a suggestion is right. This script makes that
claim true rather than decorative: it collects every ``(raw fused score, was it actually right)``
pair the corpus produces and fits the logistic in :class:`FusionConfig` by minimizing log-loss
over a grid of slopes and midpoints.

    python scripts/build_datasets.py
    python scripts/calibrate.py --write config/calibration.json

The fitted values belong in a config file that ships with the product, not in a detector body.
Re-run whenever the corpus grows or a detector's scoring changes — an uncalibrated confidence is
worse than no confidence, because users act on it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from evaluate import match_predictions  # noqa: E402

from ai_proofreader.config import DEFAULT_CONFIG, ProofreaderConfig  # noqa: E402
from ai_proofreader.pipeline import Proofreader  # noqa: E402
from ai_proofreader.testing.corruption import InjectedError  # noqa: E402

SLOPES = [round(2.0 + 0.5 * step, 2) for step in range(29)]  # 2.0 … 16.0
MIDPOINTS = [round(0.20 + 0.02 * step, 3) for step in range(36)]  # 0.20 … 0.90


def collect(dataset: Path, config: ProofreaderConfig) -> list[tuple[float, int]]:
    """Every suggestion's raw fused score paired with whether it was correct."""
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    proofreader = Proofreader(config)
    samples: list[tuple[float, int]] = []

    for entry in manifest["entries"]:
        path = dataset / (entry["corrupted"] or entry["clean"])
        errors = [InjectedError.from_dict(item) for item in entry["errors"]]
        result = proofreader.analyze_file(path)
        matches, _ = match_predictions(list(result.report.suggestions), errors)
        for match in matches:
            samples.append((match.suggestion.confidence.fused, 1 if match.is_hit else 0))
    return samples


def log_loss(samples: list[tuple[float, int]], slope: float, midpoint: float) -> float:
    total = 0.0
    for raw, label in samples:
        exponent = max(-60.0, min(60.0, -slope * (raw - midpoint)))
        probability = 1.0 / (1.0 + math.exp(exponent))
        probability = min(max(probability, 1e-6), 1 - 1e-6)
        total -= label * math.log(probability) + (1 - label) * math.log(1 - probability)
    return total / max(1, len(samples))


def fit(samples: list[tuple[float, int]]) -> tuple[float, float, float]:
    best = (DEFAULT_CONFIG.fusion.calibration_slope, DEFAULT_CONFIG.fusion.calibration_midpoint)
    best_loss = float("inf")
    for slope in SLOPES:
        for midpoint in MIDPOINTS:
            loss = log_loss(samples, slope, midpoint)
            if loss < best_loss:
                best_loss = loss
                best = (slope, midpoint)
    return best[0], best[1], best_loss


def reliability(samples: list[tuple[float, int]], slope: float, midpoint: float) -> str:
    """Predicted-versus-observed table — the only honest way to show a calibration worked."""
    buckets: dict[int, list[int]] = {}
    for raw, label in samples:
        exponent = max(-60.0, min(60.0, -slope * (raw - midpoint)))
        probability = 1.0 / (1.0 + math.exp(exponent))
        buckets.setdefault(int(probability * 10), []).append(label)

    lines = ["  predicted    n   observed", "  " + "-" * 28]
    for bucket in sorted(buckets):
        labels = buckets[bucket]
        observed = sum(labels) / len(labels)
        lines.append(
            f"  {bucket * 10:>3}-{bucket * 10 + 9:<3}  {len(labels):>4}   {observed:>7.0%}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "datasets")
    parser.add_argument("--write", type=Path, help="write a config file with the fitted values")
    args = parser.parse_args()

    if not (args.dataset / "manifest.json").is_file():
        print(f"No corpus at {args.dataset}. Run scripts/build_datasets.py first.", file=sys.stderr)
        return 2

    samples = collect(args.dataset, DEFAULT_CONFIG)
    if not samples:
        print("No suggestions produced; nothing to calibrate.", file=sys.stderr)
        return 1

    positives = sum(label for _, label in samples)
    print(f"{len(samples)} samples ({positives} correct, {len(samples) - positives} incorrect)")

    slope, midpoint, loss = fit(samples)
    baseline = log_loss(
        samples,
        DEFAULT_CONFIG.fusion.calibration_slope,
        DEFAULT_CONFIG.fusion.calibration_midpoint,
    )
    print(f"\nfitted slope={slope}  midpoint={midpoint}")
    print(f"log-loss {loss:.4f} (was {baseline:.4f} with the shipped defaults)")
    if slope in (SLOPES[0], SLOPES[-1]) or midpoint in (MIDPOINTS[0], MIDPOINTS[-1]):
        print(
            "  warning: the fit landed on the edge of the search grid, which usually means the "
            "sample is too small to pin the curve down. Treat the result as provisional."
        )
    if len(samples) < 200:
        print(
            f"  warning: {len(samples)} samples is a small basis for a calibration. The shipped "
            "defaults stay deliberately conservative until the corpus is larger."
        )
    print("\nReliability after fitting:")
    print(reliability(samples, slope, midpoint))

    if args.write:
        config = DEFAULT_CONFIG.model_copy(
            update={
                "fusion": DEFAULT_CONFIG.fusion.model_copy(
                    update={"calibration_slope": slope, "calibration_midpoint": midpoint}
                )
            }
        )
        written = config.save(args.write)
        print(f"\nWritten to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
