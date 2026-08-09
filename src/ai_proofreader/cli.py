"""Command line interface.

Four verbs:

``analyze``   run the checks and print or write a report
``apply``     apply accepted suggestions from a report and write a corrected score
``inspect``   dump the parsed structure of a score, for debugging a recognition problem
``gui``       open the review window

The ``analyze`` → edit the JSON → ``apply`` loop exists so the tool is usable in a pipeline
(batch proofreading, CI checks on a score library) without the desktop app, and so that the
review UI and the command line share exactly one code path for applying corrections.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, ProofreaderConfig
from .edits import EditApplier
from .models import SuggestionStatus
from .pipeline.proofreader import Proofreader
from .pipeline.report import load_report, render_json, render_text, write_report
from .score_parser import ProofreaderError, SourceDocument, parse_score
from .version import __version__

__all__ = ["build_parser", "main"]

logger = logging.getLogger("ai_proofreader")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-proofreader",
        description="Find and correct OMR mistakes in MuseScore output.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log detector detail")
    parser.add_argument("--config", type=Path, help="JSON configuration file")
    parser.add_argument(
        "--musescore", help="path to the MuseScore executable, for .mscz input/output"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="check a score and report suspected errors")
    analyze.add_argument("score", type=Path, help=".musicxml, .mxl or .mscz file")
    analyze.add_argument("-o", "--output", type=Path, help="write the report (.json/.html/.txt)")
    analyze.add_argument(
        "--min-confidence",
        type=float,
        help="hide suggestions below this confidence (0-1); overrides the config",
    )
    analyze.add_argument("--json", action="store_true", help="print JSON instead of text")
    analyze.add_argument("--no-parallel", action="store_true", help="run detectors serially")

    apply_command = subparsers.add_parser("apply", help="apply corrections and write a new score")
    apply_command.add_argument("score", type=Path)
    apply_command.add_argument("-o", "--output", type=Path, required=True)
    apply_command.add_argument(
        "--report", type=Path, help="JSON report to take suggestions from (default: re-analyse)"
    )
    apply_command.add_argument(
        "--accept",
        nargs="*",
        default=None,
        metavar="ID",
        help="suggestion ids to apply; omit to apply everything marked accepted in the report",
    )
    apply_command.add_argument(
        "--accept-above",
        type=float,
        metavar="CONFIDENCE",
        help="apply every suggestion at or above this confidence. Use deliberately: it is the "
        "one path in this tool that changes a score without a human looking at each case.",
    )
    apply_command.add_argument(
        "--dry-run", action="store_true", help="report what would change and write nothing"
    )

    inspect = subparsers.add_parser("inspect", help="print the parsed structure of a score")
    inspect.add_argument("score", type=Path)
    inspect.add_argument("--measures", help="range such as 1-8")
    inspect.add_argument("--part", help="restrict to one part id")

    gui = subparsers.add_parser("gui", help="open the review window")
    gui.add_argument("score", type=Path, nargs="?", help="score to open on start")
    gui.add_argument("--scan", type=Path, help="scanned PDF or image to show alongside")

    return parser


def _load_config(args: argparse.Namespace) -> ProofreaderConfig:
    config = ProofreaderConfig.load(args.config) if args.config else DEFAULT_CONFIG
    analysis_updates: dict[str, object] = {}
    if getattr(args, "min_confidence", None) is not None:
        analysis_updates["min_confidence"] = args.min_confidence
    if getattr(args, "no_parallel", False):
        analysis_updates["parallel"] = False
    if analysis_updates:
        config = config.model_copy(
            update={"analysis": config.analysis.model_copy(update=analysis_updates)}
        )
    return config


def _command_analyze(args: argparse.Namespace) -> int:
    config = _load_config(args)
    proofreader = Proofreader(config)
    result = proofreader.analyze_file(args.score, musescore_binary=args.musescore)

    if args.output:
        written = write_report(result.report, args.output)
        print(f"Report written to {written}")
    if args.json:
        print(render_json(result.report))
    elif not args.output or args.verbose:
        print(render_text(result.report, verbose=args.verbose))
    return 0


def _command_apply(args: argparse.Namespace) -> int:
    config = _load_config(args)
    document = SourceDocument.load(args.score, musescore_binary=args.musescore)
    score = parse_score(document)

    if args.report:
        report = load_report(args.report)
    else:
        report = Proofreader(config).analyze(score, document).report

    chosen = _select(report, args)
    if not chosen:
        print("Nothing to apply.")
        return 0

    applier = EditApplier(document)
    applied = 0
    skipped: list[str] = []
    for suggestion in chosen:
        if not suggestion.is_actionable:
            skipped.append(f"{suggestion.id}: advisory only, no automatic correction")
            continue
        try:
            applier.apply_suggestion(suggestion)
            applied += 1
            print(f"  applied {suggestion.id}: {suggestion.one_line()}")
        except ProofreaderError as exc:
            skipped.append(f"{suggestion.id}: {exc}")
        if applier.has_structural_changes:
            # Inserting or deleting elements shifts every handle after it, so anything further
            # would target the wrong note. Stop and tell the user to re-run.
            skipped.extend(
                f"{other.id}: skipped, re-run analysis after a structural change"
                for other in chosen[chosen.index(suggestion) + 1 :]
            )
            break

    for message in skipped:
        print(f"  skipped {message}", file=sys.stderr)

    if args.dry_run:
        print(f"Dry run: {applied} correction(s) would be applied; nothing written.")
        applier.undo_all()
        return 0

    written = document.save(args.output, musescore_binary=args.musescore)
    print(f"{applied} correction(s) applied. Corrected score written to {written}")
    return 0


def _select(report: object, args: argparse.Namespace) -> list:  # type: ignore[type-arg]
    suggestions = list(getattr(report, "suggestions", ()))
    if args.accept:
        wanted = set(args.accept)
        return [suggestion for suggestion in suggestions if suggestion.id in wanted]
    if args.accept_above is not None:
        return [
            suggestion
            for suggestion in suggestions
            if suggestion.confidence.calibrated >= args.accept_above
        ]
    return [
        suggestion for suggestion in suggestions if suggestion.status is SuggestionStatus.ACCEPTED
    ]


def _command_inspect(args: argparse.Namespace) -> int:
    document = SourceDocument.load(args.score, musescore_binary=args.musescore)
    score = parse_score(document)
    print(score.summary())
    if score.issues:
        print("\nParse issues:")
        for issue in score.issues:
            print(f"  [{issue.severity}] {issue.message} ({issue.detail or 'no detail'})")

    low, high = _measure_range(args.measures)
    for part in score.parts:
        if args.part and part.id != args.part:
            continue
        transpose = (
            f", transposing {part.transpose.chromatic:+d} semitones" if part.is_transposing else ""
        )
        print(f"\n{part.id} — {part.display_name} ({part.staves} staff/staves{transpose})")
        for measure in part.measures:
            if not low <= measure.index + 1 <= high:
                continue
            attributes = measure.attributes
            time = attributes.time.describe() if attributes.time else "—"
            print(
                f"  m.{measure.number:<4} key={attributes.key.describe():<18} time={time:<8} "
                f"voices={','.join(measure.voices) or '—'}"
            )
            for event in measure.events:
                print(
                    f"      {event.onset!s:>6}  v{event.voice} s{event.staff}  "
                    f"{event.describe()}"
                )
    return 0


def _measure_range(text: str | None) -> tuple[int, int]:
    if not text:
        return (1, 10**9)
    if "-" in text:
        start, _, end = text.partition("-")
        return (int(start), int(end))
    value = int(text)
    return (value, value)


def _command_gui(args: argparse.Namespace) -> int:
    try:
        from .ui.app import run
    except ImportError as exc:  # pragma: no cover - depends on optional install
        print(
            "The review window needs PySide6. Install it with:\n"
            "    pip install 'ai-musical-proofreader[ui]'\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 2
    return run(score_path=args.score, scan_path=args.scan, config=_load_config(args))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    commands = {
        "analyze": _command_analyze,
        "apply": _command_apply,
        "inspect": _command_inspect,
        "gui": _command_gui,
    }
    try:
        return commands[args.command](args)
    except ProofreaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
