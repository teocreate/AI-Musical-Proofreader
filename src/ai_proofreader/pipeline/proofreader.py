"""Analysis orchestration.

Owns the run: build the context once, execute every enabled detector, fuse the evidence, merge
duplicates, rank, and report. It contains no musical logic — anything that decides *whether* a
note is wrong belongs in a detector, and anything that decides *how sure* we are belongs in
:mod:`ai_proofreader.confidence`.

Two things here earn their complexity:

* **Detector isolation.** Every rule runs inside a try/except. One rule raising on a pathological
  score must not lose the other thirteen rules' findings — those findings are the product.
* **Part sharding.** Beyond a size threshold, part-local detectors are run across processes, one
  shard per part. Cross-part rules (vertical harmony) stay in the parent. Each worker receives a
  score containing *only its own part* — sending the whole score to every worker costs more in
  pickling than the parallelism saves, which on a 16-part score is the difference between 19
  seconds and 6.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from ..analysis import AnalysisContext, Detector, build_detectors
from ..confidence import Verifier, apply_confidence, merge_duplicates, rank
from ..confidence.channels import VerificationRequest
from ..config import DEFAULT_CONFIG, AnalysisConfig, ProofreaderConfig
from ..models import AnalysisReport, Score, Suggestion
from ..score_parser import SourceDocument, parse_score
from ..version import __version__

__all__ = ["ProofreadResult", "Proofreader"]

logger = logging.getLogger(__name__)


@dataclass
class ProofreadResult:
    """Everything one analysis produced, including the objects needed to apply corrections."""

    score: Score
    #: ``None`` when the score was analysed in memory rather than loaded from a file. Applying
    #: corrections needs a document, so the UI and CLI always load through one.
    document: SourceDocument | None
    report: AnalysisReport
    context: AnalysisContext

    @property
    def suggestions(self) -> tuple[Suggestion, ...]:
        return self.report.suggestions


class Proofreader:
    """Runs the analysis pipeline over a score."""

    def __init__(
        self,
        config: ProofreaderConfig | None = None,
        verifiers: Sequence[Verifier] = (),
    ) -> None:
        self.config = config or DEFAULT_CONFIG
        self.verifiers = tuple(verifiers)

    # -- entry points --------------------------------------------------------------

    def analyze_file(
        self, path: str | Path, musescore_binary: str | None = None
    ) -> ProofreadResult:
        """Load, parse and analyse a score file."""
        document = SourceDocument.load(path, musescore_binary=musescore_binary)
        score = parse_score(document)
        return self.analyze(score, document)

    def analyze(self, score: Score, document: SourceDocument | None = None) -> ProofreadResult:
        """Analyse an already-parsed score."""
        started = perf_counter()
        context = AnalysisContext(score, self.config.analysis)

        raw, timings = self._collect(score, context)
        verified = self._verify(raw)
        scored = apply_confidence(verified, self.config.fusion)
        merged = merge_duplicates(scored)
        # Merging unions evidence, so confidence has to be recomputed on the merged items.
        rescored = apply_confidence(merged, self.config.fusion)
        ranked = rank(
            rescored,
            minimum=self.config.analysis.min_confidence,
            limit=self.config.analysis.max_suggestions,
        )

        report = AnalysisReport(
            score_summary=score.summary(),
            source_path=str(document.path) if document else score.metadata.source_path,
            suggestions=tuple(ranked),
            parse_issues=tuple(issue.message for issue in score.issues),
            detector_timings_ms=timings,
            total_ms=(perf_counter() - started) * 1000,
            note_count=context.note_count,
            measure_count=score.measure_count,
            version=__version__,
        )
        return ProofreadResult(score=score, document=document, report=report, context=context)

    # -- detector execution --------------------------------------------------------

    def _collect(
        self, score: Score, context: AnalysisContext
    ) -> tuple[list[Suggestion], dict[str, float]]:
        detectors = build_detectors(self.config.analysis)
        should_shard = (
            self.config.analysis.parallel
            and len(score.parts) > 1
            and context.note_count >= self.config.analysis.parallel_threshold_notes
        )
        if not should_shard:
            return _run_detectors(detectors, context)

        cross_part = [detector for detector in detectors if detector.cross_part]
        local = [detector for detector in detectors if not detector.cross_part]

        suggestions, timings = _run_detectors(cross_part, context)
        shard_results = self._run_sharded(score, local)
        suggestions.extend(shard_results[0])
        for name, elapsed in shard_results[1].items():
            timings[name] = timings.get(name, 0.0) + elapsed
        return suggestions, timings

    def _run_sharded(
        self, score: Score, detectors: Sequence[Detector]
    ) -> tuple[list[Suggestion], dict[str, float]]:
        names = [detector.name for detector in detectors]
        workers = min(len(score.parts), os.cpu_count() or 2)
        # Slice the score in the parent: a worker only ever needs its own part, and shipping the
        # rest is pure serialization cost paid once per worker.
        payloads = [
            (score.model_copy(update={"parts": (part,)}), self.config.analysis, names)
            for part in score.parts
        ]

        suggestions: list[Suggestion] = []
        timings: dict[str, float] = {}
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for shard_suggestions, shard_timings in pool.map(_shard_worker, payloads):
                    suggestions.extend(shard_suggestions)
                    for name, elapsed in shard_timings.items():
                        timings[name] = timings.get(name, 0.0) + elapsed
        except Exception:  # pragma: no cover - falls back rather than failing the run
            logger.warning("parallel analysis unavailable, falling back to serial", exc_info=True)
            context = AnalysisContext(score, self.config.analysis)
            return _run_detectors(list(detectors), context)
        return suggestions, timings

    # -- verification channels -----------------------------------------------------

    def _verify(self, suggestions: Sequence[Suggestion]) -> list[Suggestion]:
        """Offer each suggestion to every configured verifier.

        Phase 1 ships no verifiers, so this is a pass-through. The loop exists now so that adding
        the CV or AI channel later is a registration, not a pipeline change.
        """
        if not self.verifiers:
            return list(suggestions)

        results: list[Suggestion] = []
        for suggestion in suggestions:
            evidence = list(suggestion.evidence)
            for verifier in self.verifiers:
                try:
                    item = verifier.verify(VerificationRequest(suggestion))
                except Exception:  # pragma: no cover - a broken channel must not stop the run
                    logger.exception("verifier %s failed", getattr(verifier, "name", verifier))
                    continue
                if item is not None:
                    evidence.append(item)
            results.append(suggestion.model_copy(update={"evidence": tuple(evidence)}))
        return results


def _run_detectors(
    detectors: Sequence[Detector], context: AnalysisContext
) -> tuple[list[Suggestion], dict[str, float]]:
    """Run detectors serially, timing each and isolating failures."""
    suggestions: list[Suggestion] = []
    timings: dict[str, float] = {}
    for detector in detectors:
        started = perf_counter()
        try:
            produced = list(detector.run(context))
        except Exception:
            logger.exception("detector %s failed and was skipped", detector.name)
            produced = []
        timings[detector.name] = (perf_counter() - started) * 1000
        suggestions.extend(produced)
    return suggestions, timings


def _shard_worker(
    payload: tuple[Score, AnalysisConfig, list[str]],
) -> tuple[list[Suggestion], dict[str, float]]:
    """Analyse one part in a worker process.

    Module-level so it can be pickled. The shard arrives already reduced to a single part;
    element handles are plain integers and stay valid in the parent.
    """
    shard, config, names = payload
    context = AnalysisContext(shard, config)
    wanted = set(names)
    detectors = [detector for detector in build_detectors(config) if detector.name in wanted]
    return _run_detectors(detectors, context)
