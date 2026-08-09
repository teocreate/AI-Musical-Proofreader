#!/usr/bin/env python3
"""Build the regression corpus under ``datasets/``.

Writes, for every entry in :mod:`ai_proofreader.testing.corpus`:

* ``clean/<name>.musicxml``      — the correct score
* ``corrupted/<name>.s<seed>.musicxml`` — copies with known errors injected
* ``manifest.json``              — what was injected, where, and what should be reported

The manifest is the ground truth ``scripts/evaluate.py`` scores against. Regenerating is
deterministic: same seeds, same corpus, same files, so a diff in the manifest always means a
deliberate change to the corpus or a change in behaviour of the injection code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_proofreader.score_parser import SourceDocument, parse_score  # noqa: E402
from ai_proofreader.testing import (  # noqa: E402
    Corruptor,
    ErrorKind,
    build_corpus_score,
    corpus_names,
    write_musicxml,
)

#: Errors injected into each corpus entry, and how many of each run to attempt.
INJECTION_PLAN: dict[str, tuple[tuple[ErrorKind, ...], int]] = {
    "chorale": (
        (
            ErrorKind.NOTEHEAD_SHIFT,
            ErrorKind.MISSING_ACCIDENTAL,
            ErrorKind.SPURIOUS_ACCIDENTAL,
            ErrorKind.HALVED_DURATION,
        ),
        6,
    ),
    "piano": (
        (
            ErrorKind.NOTEHEAD_SHIFT,
            ErrorKind.MISSING_ACCIDENTAL,
            ErrorKind.HALVED_DURATION,
            ErrorKind.MISSING_DOT,
            ErrorKind.DROPPED_REST,
        ),
        8,
    ),
    "trio": (
        (
            ErrorKind.NOTEHEAD_SHIFT,
            ErrorKind.MISSING_ACCIDENTAL,
            ErrorKind.SPURIOUS_ACCIDENTAL,
            ErrorKind.HALVED_DURATION,
        ),
        5,
    ),
    # The negative control gets no injections: its whole job is to measure false positives.
    "chromatic": ((), 0),
}

SEEDS = (1, 2, 3)


def build(output: Path) -> dict[str, object]:
    clean_dir = output / "clean"
    corrupt_dir = output / "corrupted"
    clean_dir.mkdir(parents=True, exist_ok=True)
    corrupt_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    for name in corpus_names():
        clean_path = clean_dir / f"{name}.musicxml"
        write_musicxml(build_corpus_score(name), str(clean_path))
        kinds, count = INJECTION_PLAN.get(name, ((), 0))

        entries.append(
            {
                "name": name,
                "role": "negative-control" if not kinds else "positive",
                "clean": clean_path.relative_to(output).as_posix(),
                "corrupted": None,
                "errors": [],
            }
        )
        if not kinds:
            print(f"  {name:<10} clean only (negative control)")
            continue

        for seed in SEEDS:
            document = SourceDocument.load(clean_path)
            score = parse_score(document)
            corruptor = Corruptor(document, score, seed=seed)
            result = corruptor.inject(kinds, count)
            target = corrupt_dir / f"{name}.s{seed}.musicxml"
            result.document.save(target)
            entries.append(
                {
                    "name": f"{name}.s{seed}",
                    "role": "positive",
                    "clean": clean_path.relative_to(output).as_posix(),
                    "corrupted": target.relative_to(output).as_posix(),
                    "errors": [error.to_dict() for error in result.errors],
                }
            )
            print(f"  {name:<10} seed {seed}: {result.count} error(s) injected")

    manifest = {
        "version": 1,
        "seeds": list(SEEDS),
        "entries": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "datasets", help="where to write the corpus"
    )
    args = parser.parse_args()

    print(f"Building regression corpus in {args.output}")
    manifest = build(args.output)
    positives = sum(len(entry["errors"]) for entry in manifest["entries"])  # type: ignore[arg-type]
    print(f"\n{len(manifest['entries'])} entries, {positives} injected errors total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
