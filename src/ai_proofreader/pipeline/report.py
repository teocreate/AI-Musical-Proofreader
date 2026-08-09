"""Report rendering.

Three formats, three audiences: a terminal summary for the person who just ran the CLI, JSON for
anything downstream (the UI's session state, CI gates, the evaluation harness), and a standalone
HTML page for sending to whoever is doing the proofreading.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from pathlib import Path

from ..models import AnalysisReport, Severity, Suggestion

__all__ = ["render_html", "render_json", "render_text", "write_report"]

_SEVERITY_COLOURS = {
    Severity.CRITICAL: "#c0392b",
    Severity.HIGH: "#d35400",
    Severity.MEDIUM: "#b7950b",
    Severity.LOW: "#5d6d7e",
}


def render_text(report: AnalysisReport, verbose: bool = False) -> str:
    """Terminal summary."""
    lines: list[str] = []
    lines.append(report.score_summary or "(no score summary)")
    lines.append(
        f"{len(report.suggestions)} suggestion(s) in {report.total_ms:.0f} ms "
        f"across {report.measure_count} measures / {report.note_count} notes"
    )
    if report.parse_issues:
        lines.append("")
        lines.append("Parse issues:")
        lines.extend(f"  ! {issue}" for issue in report.parse_issues)

    counts = report.by_kind()
    if counts:
        lines.append("")
        lines.append(
            "By type: "
            + ", ".join(
                f"{kind.label} {count}"
                for kind, count in sorted(counts.items(), key=lambda item: -item[1])
            )
        )

    lines.append("")
    for suggestion in report.suggestions:
        lines.append(_text_entry(suggestion, verbose))
    if not report.suggestions:
        lines.append("No suspected recognition errors above the confidence threshold.")

    if verbose and report.detector_timings_ms:
        lines.append("")
        lines.append("Detector timings (ms):")
        for name, elapsed in sorted(report.detector_timings_ms.items(), key=lambda item: -item[1]):
            lines.append(f"  {name:<28} {elapsed:8.1f}")
    return "\n".join(lines)


def _text_entry(suggestion: Suggestion, verbose: bool) -> str:
    header = (
        f"[{suggestion.confidence.percent:3d}%] {suggestion.severity.value:<8} "
        f"{suggestion.target.locator()}"
    )
    change = (
        f"    {suggestion.current_repr} -> {suggestion.suggested_repr}"
        if suggestion.suggested_repr
        else "    (no automatic correction — review by hand)"
    )
    body = [header, f"    {suggestion.title}", change]
    if verbose:
        body.append(f"    id={suggestion.id} detector={suggestion.detector}")
        body.append(f"    {suggestion.explanation}")
        for item in suggestion.evidence:
            body.append(f"      · [{item.channel.value}{item.score:.2f}] {item.summary}")
    return "\n".join(body)


def render_json(report: AnalysisReport, indent: int = 2) -> str:
    return report.model_dump_json(indent=indent)


def render_html(report: AnalysisReport) -> str:
    """Self-contained HTML report, no external assets."""
    rows: list[str] = []
    for suggestion in report.suggestions:
        colour = _SEVERITY_COLOURS.get(suggestion.severity, "#5d6d7e")
        evidence = "".join(
            f"<li><span class='chan'>{html.escape(item.channel.value)}</span> "
            f"{item.score:.2f} — {html.escape(item.summary)}</li>"
            for item in suggestion.evidence
        )
        rows.append(f"""
        <article class="suggestion">
          <header>
            <span class="confidence">{suggestion.confidence.percent}%</span>
            <span class="badge" style="background:{colour}">{suggestion.severity.value}</span>
            <span class="locator">{html.escape(suggestion.target.locator())}</span>
          </header>
          <h3>{html.escape(suggestion.title)}</h3>
          <p class="change"><code>{html.escape(suggestion.current_repr or "—")}</code>
             <span class="arrow">→</span>
             <code>{html.escape(suggestion.suggested_repr or "review by hand")}</code></p>
          <p class="explanation">{html.escape(suggestion.explanation)}</p>
          <ul class="evidence">{evidence}</ul>
        </article>""")

    counts = ", ".join(
        f"{kind.label}: {count}"
        for kind, count in sorted(report.by_kind().items(), key=lambda item: -item[1])
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Proofreading report — {html.escape(report.score_summary)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.55 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0 auto;
          max-width: 60rem; padding: 2rem 1.25rem; }}
  h1 {{ font-size: 1.4rem; margin-bottom: .25rem; }}
  .meta {{ color: #6b7280; margin-bottom: 1.5rem; }}
  .suggestion {{ border: 1px solid #d1d5db55; border-radius: 10px; padding: 1rem 1.15rem;
                 margin-bottom: .9rem; }}
  .suggestion header {{ display: flex; gap: .6rem; align-items: center; margin-bottom: .35rem; }}
  .confidence {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
  .badge {{ color: #fff; border-radius: 999px; padding: .05rem .55rem; font-size: .72rem;
            text-transform: uppercase; letter-spacing: .04em; }}
  .locator {{ color: #6b7280; font-size: .85rem; }}
  h3 {{ font-size: 1rem; margin: .1rem 0 .4rem; }}
  .change code {{ background: #8881; padding: .1rem .4rem; border-radius: 4px; }}
  .arrow {{ opacity: .55; margin: 0 .35rem; }}
  .explanation {{ margin: .5rem 0; }}
  .evidence {{ margin: .4rem 0 0; padding-left: 1.1rem; color: #6b7280; font-size: .87rem; }}
  .chan {{ text-transform: uppercase; font-size: .7rem; letter-spacing: .05em; }}
</style></head><body>
<h1>{html.escape(report.score_summary or "Proofreading report")}</h1>
<p class="meta">{len(report.suggestions)} suggestion(s) · {report.measure_count} measures ·
   {report.note_count} notes · analysed in {report.total_ms:.0f} ms ·
   ai-musical-proofreader {html.escape(report.version)}<br>{html.escape(counts)}</p>
{"".join(rows) or "<p>No suspected recognition errors above the confidence threshold.</p>"}
</body></html>"""


def write_report(report: AnalysisReport, path: str | Path) -> Path:
    """Write a report, choosing the format from the file extension."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()
    if suffix == ".json":
        target.write_text(render_json(report), encoding="utf-8")
    elif suffix in {".html", ".htm"}:
        target.write_text(render_html(report), encoding="utf-8")
    else:
        target.write_text(render_text(report, verbose=True), encoding="utf-8")
    return target


def load_report(path: str | Path) -> AnalysisReport:
    """Read a JSON report back, for the apply workflow."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return AnalysisReport.model_validate(data)


def summarize(suggestions: Iterable[Suggestion]) -> str:
    """One-line summary used in log output and commit messages."""
    items = list(suggestions)
    if not items:
        return "no suggestions"
    top = max(items, key=lambda suggestion: suggestion.confidence.calibrated)
    return f"{len(items)} suggestions, highest confidence {top.confidence.percent}% ({top.title})"
