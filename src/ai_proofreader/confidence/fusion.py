"""Confidence fusion and calibration.

A percentage printed next to a suggestion is a claim about how often that suggestion is right.
Three rules keep the claim honest:

1. **Absent is not zero.** A channel that did not run (no scan loaded, AI verification disabled)
   must not drag the score down, and neither must a channel that agrees only weakly. The
   strongest channel sets the level and agreeing channels add a bounded lift on top — averaging
   would mean that a second rule *agreeing* with the first made us less sure, which is nonsense.
2. **Contradiction costs, disagreement in strength does not.** These are different things. Two
   rules supporting the same fix at 0.5 and 0.8 agree. A CV check reporting that the page plainly
   shows the existing notation *contradicts*, and that is what collapses the score — in
   proportion to how sure the contradicting channel is.
3. **Musical reasoning alone has a ceiling.** Without having looked at the page, no amount of
   theory should produce 97%. The ceiling is configurable and applies whenever the visual channel
   is absent.

The final mapping from fused score to displayed percentage is an explicit logistic whose
parameters are fitted on the regression corpus by ``scripts/evaluate.py``, not hand-picked.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..config import FusionConfig
from ..models import Channel, ConfidenceBreakdown, Evidence, Suggestion

__all__ = [
    "apply_confidence",
    "calibrate",
    "channel_scores",
    "fuse_evidence",
    "merge_duplicates",
    "rank",
    "split_evidence",
]


def calibrate(raw: float, config: FusionConfig) -> float:
    """Map a fused score onto a displayed probability with a logistic curve."""
    exponent = -config.calibration_slope * (raw - config.calibration_midpoint)
    # Guard against overflow for extreme slopes.
    if exponent > 60:
        return 0.0
    if exponent < -60:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def split_evidence(
    evidence: Iterable[Evidence],
) -> tuple[dict[Channel, float], dict[Channel, float]]:
    """Collapse evidence into per-channel supporting and contrary strengths.

    Within a channel, items of the same polarity combine with a noisy-OR: two independent weak
    reasons are stronger than either alone, but never certain.
    """
    supporting: dict[Channel, float] = {}
    contrary: dict[Channel, float] = {}
    for item in evidence:
        bucket = contrary if item.supports_current_reading else supporting
        previous = bucket.get(item.channel, 0.0)
        bucket[item.channel] = previous + item.score - previous * item.score
    return supporting, contrary


def channel_scores(evidence: Iterable[Evidence]) -> dict[Channel, float]:
    """Net per-channel score: what the channel supports, less what it contradicts."""
    supporting, contrary = split_evidence(evidence)
    scores: dict[Channel, float] = {}
    for channel in set(supporting) | set(contrary):
        value = supporting.get(channel, 0.0) - contrary.get(channel, 0.0)
        scores[channel] = max(0.0, min(1.0, value))
    return scores


def fuse_evidence(
    evidence: Sequence[Evidence], config: FusionConfig | None = None
) -> ConfidenceBreakdown:
    """Combine per-channel scores into a calibrated confidence."""
    settings = config or FusionConfig()
    supporting, contrary = split_evidence(evidence)
    scores = channel_scores(evidence)
    if not scores:
        return ConfidenceBreakdown()

    weights = {
        Channel.MUSICAL: settings.musical_weight,
        Channel.PATTERN: settings.pattern_weight,
        Channel.VISUAL: settings.visual_weight,
        Channel.AI: settings.ai_weight,
    }

    # The best-supported channel anchors the result; the rest can only add to it. This is what
    # makes a second, weaker rule agreeing with the first a good thing rather than a dilution.
    anchor_channel = max(scores, key=lambda channel: scores[channel])
    raw = scores[anchor_channel]

    others = {channel: value for channel, value in scores.items() if channel is not anchor_channel}
    if others:
        total_weight = sum(weights[channel] for channel in others)
        corroboration = (
            sum(value * weights[channel] for channel, value in others.items()) / total_weight
            if total_weight
            else 0.0
        )
        raw += (1.0 - raw) * corroboration * settings.corroboration_gain

    # Contradiction is the only thing that can pull the score down, and it does so hard: a
    # verifier reporting that the page plainly shows the existing notation is the single most
    # informative signal available, because it is the one that looked.
    contradiction = max(contrary.values(), default=0.0)
    if contradiction > 0 and supporting:
        raw *= 1.0 - contradiction * settings.disagreement_penalty

    raw = max(0.0, min(1.0, raw))
    calibrated = calibrate(raw, settings)
    if Channel.VISUAL not in scores:
        # Nothing has looked at the page, so cap what we are willing to claim about it.
        raw = min(raw, settings.musical_only_ceiling)
        calibrated = min(calibrated, settings.musical_only_ceiling)

    return ConfidenceBreakdown(
        musical=scores.get(Channel.MUSICAL),
        pattern=scores.get(Channel.PATTERN),
        visual=scores.get(Channel.VISUAL),
        ai=scores.get(Channel.AI),
        fused=raw,
        calibrated=calibrated,
        disagreement=contradiction,
    )


def apply_confidence(
    suggestions: Iterable[Suggestion], config: FusionConfig | None = None
) -> list[Suggestion]:
    """Attach fused confidence to every suggestion."""
    settings = config or FusionConfig()
    return [
        suggestion.with_confidence(fuse_evidence(suggestion.evidence, settings))
        for suggestion in suggestions
    ]


def merge_duplicates(suggestions: Sequence[Suggestion]) -> list[Suggestion]:
    """Merge suggestions that propose the same change to the same place.

    Two detectors independently reaching the same conclusion is *stronger* evidence than either
    alone, and showing it twice would be both noisy and misleading. The merged suggestion keeps
    the higher severity, unions the evidence, and takes the clearer of the two explanations.
    """
    merged: dict[tuple[str, int, str, str], Suggestion] = {}
    order: list[tuple[str, int, str, str]] = []

    for suggestion in suggestions:
        signature = (
            suggestion.target.part_id,
            suggestion.target.measure_index,
            str(suggestion.target.onset),
            _edit_signature(suggestion),
        )
        existing = merged.get(signature)
        if existing is None:
            merged[signature] = suggestion
            order.append(signature)
            continue

        detectors = {item.detector for item in existing.evidence} | {
            item.detector for item in suggestion.evidence
        }
        primary, secondary = (
            (existing, suggestion)
            if len(existing.explanation) >= len(suggestion.explanation)
            else (suggestion, existing)
        )
        merged[signature] = primary.model_copy(
            update={
                "evidence": existing.evidence + suggestion.evidence,
                "severity": max(
                    existing.severity, suggestion.severity, key=lambda level: level.rank
                ),
                "detector": "+".join(sorted(detectors)),
                "explanation": (
                    f"{primary.explanation}\n\nAlso flagged independently: "
                    f"{secondary.explanation}"
                    if secondary.explanation != primary.explanation
                    else primary.explanation
                ),
            }
        )

    return [merged[signature] for signature in order]


def _edit_signature(suggestion: Suggestion) -> str:
    """What a suggestion would actually do, as a comparable string."""
    if not suggestion.edits:
        return f"advisory:{suggestion.kind.value}"
    return "|".join(
        f"{edit.op}:{edit.ref}:{edit.describe()}" for edit in suggestion.edits  # type: ignore[union-attr]
    )


def rank(
    suggestions: Iterable[Suggestion], minimum: float = 0.0, limit: int | None = None
) -> list[Suggestion]:
    """Filter by confidence and order for presentation."""
    kept = [suggestion for suggestion in suggestions if suggestion.confidence.calibrated >= minimum]
    kept.sort(key=lambda suggestion: suggestion.sort_key)
    return kept[:limit] if limit else kept
