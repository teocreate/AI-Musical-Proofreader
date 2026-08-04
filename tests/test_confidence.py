"""Confidence fusion tests.

The three properties that make a printed percentage honest: absent channels do not count as
zero, disagreement costs, and musical reasoning alone cannot claim near-certainty about a page it
has not seen.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from ai_proofreader.confidence import (
    NullVerifier,
    VerificationRequest,
    apply_confidence,
    calibrate,
    channel_scores,
    fuse_evidence,
    merge_duplicates,
    rank,
)
from ai_proofreader.config import FusionConfig
from ai_proofreader.models import (
    Channel,
    Evidence,
    SetPitchOp,
    Severity,
    Step,
    Suggestion,
    SuggestionKind,
    SuggestionTarget,
)


def evidence(channel: Channel, score: float, contrary: bool = False) -> Evidence:
    return Evidence(channel=channel, score=score, summary="test", supports_current_reading=contrary)


def suggestion(
    identifier: str = "s1",
    items: tuple[Evidence, ...] = (),
    measure: int = 0,
    ref: int = 1,
    explanation: str = "because",
) -> Suggestion:
    return Suggestion(
        id=identifier,
        kind=SuggestionKind.PITCH,
        severity=Severity.MEDIUM,
        title="test",
        explanation=explanation,
        target=SuggestionTarget(part_id="P1", measure_index=measure, onset=Fraction(0)),
        evidence=items,
        edits=(SetPitchOp(ref=ref, step=Step.C, alter=0, octave=4),),
    )


class TestChannelScores:
    def test_two_weak_reasons_beat_one(self) -> None:
        single = channel_scores([evidence(Channel.MUSICAL, 0.5)])[Channel.MUSICAL]
        double = channel_scores([evidence(Channel.MUSICAL, 0.5), evidence(Channel.MUSICAL, 0.5)])[
            Channel.MUSICAL
        ]
        assert double > single
        assert double < 1.0

    def test_contrary_evidence_subtracts(self) -> None:
        scores = channel_scores(
            [evidence(Channel.VISUAL, 0.9), evidence(Channel.VISUAL, 0.6, contrary=True)]
        )
        assert scores[Channel.VISUAL] == pytest.approx(0.3)

    def test_scores_stay_in_range(self) -> None:
        scores = channel_scores([evidence(Channel.VISUAL, 0.2, contrary=True)])
        assert scores[Channel.VISUAL] == 0.0


class TestFusion:
    def test_absent_channels_do_not_drag_the_score_down(self) -> None:
        """A score analysed with no scan must not be penalized for the scan's absence."""
        only_musical = fuse_evidence([evidence(Channel.MUSICAL, 0.8)])
        assert only_musical.fused == pytest.approx(0.8)
        assert only_musical.visual is None
        assert only_musical.ai is None

    def test_agreeing_channels_reinforce(self) -> None:
        alone = fuse_evidence([evidence(Channel.MUSICAL, 0.8)])
        together = fuse_evidence([evidence(Channel.MUSICAL, 0.8), evidence(Channel.VISUAL, 0.9)])
        assert together.calibrated > alone.calibrated

    def test_contradicting_visual_evidence_collapses_confidence(self) -> None:
        confident = fuse_evidence([evidence(Channel.MUSICAL, 0.85)])
        contradicted = fuse_evidence(
            [evidence(Channel.MUSICAL, 0.85), evidence(Channel.VISUAL, 0.9, contrary=True)]
        )
        assert contradicted.calibrated < confident.calibrated / 2
        assert contradicted.disagreement > 0.5

    def test_musical_reasoning_alone_has_a_ceiling(self) -> None:
        config = FusionConfig(musical_only_ceiling=0.85)
        certain = fuse_evidence([evidence(Channel.MUSICAL, 1.0)], config)
        assert certain.calibrated <= 0.85

    def test_the_ceiling_lifts_once_the_page_has_been_looked_at(self) -> None:
        config = FusionConfig(musical_only_ceiling=0.85)
        confirmed = fuse_evidence(
            [evidence(Channel.MUSICAL, 1.0), evidence(Channel.VISUAL, 1.0)], config
        )
        assert confirmed.calibrated > 0.85

    def test_no_evidence_means_no_confidence(self) -> None:
        assert fuse_evidence([]).calibrated == 0.0

    def test_percent_is_the_calibrated_value(self) -> None:
        result = fuse_evidence([evidence(Channel.MUSICAL, 0.8)])
        assert result.percent == round(result.calibrated * 100)


class TestCalibration:
    def test_is_monotonic(self) -> None:
        config = FusionConfig()
        values = [calibrate(raw / 20, config) for raw in range(21)]
        assert values == sorted(values)

    def test_midpoint_maps_to_half(self) -> None:
        config = FusionConfig(calibration_midpoint=0.5)
        assert calibrate(0.5, config) == pytest.approx(0.5)

    def test_extremes_do_not_overflow(self) -> None:
        config = FusionConfig(calibration_slope=1000.0)
        assert calibrate(0.0, config) == 0.0
        assert calibrate(1.0, config) == 1.0


class TestMerging:
    def test_same_fix_from_two_detectors_becomes_one_entry(self) -> None:
        first = suggestion("a", (evidence(Channel.MUSICAL, 0.7),), explanation="reason one")
        second = suggestion("b", (evidence(Channel.PATTERN, 0.8),), explanation="reason two")
        merged = merge_duplicates([first, second])
        assert len(merged) == 1
        assert len(merged[0].evidence) == 2
        assert "reason one" in merged[0].explanation
        assert "reason two" in merged[0].explanation

    def test_merging_raises_confidence(self) -> None:
        first = suggestion("a", (evidence(Channel.MUSICAL, 0.7),), explanation="one")
        second = suggestion("b", (evidence(Channel.PATTERN, 0.8),), explanation="two")
        alone = apply_confidence([first])[0]
        together = apply_confidence(merge_duplicates([first, second]))[0]
        assert together.confidence.calibrated > alone.confidence.calibrated

    def test_different_places_stay_separate(self) -> None:
        first = suggestion("a", (evidence(Channel.MUSICAL, 0.7),), measure=0, ref=1)
        second = suggestion("b", (evidence(Channel.MUSICAL, 0.7),), measure=4, ref=2)
        assert len(merge_duplicates([first, second])) == 2


class TestRanking:
    def test_filters_and_orders_by_confidence(self) -> None:
        low = apply_confidence([suggestion("low", (evidence(Channel.MUSICAL, 0.2),))])[0]
        high = apply_confidence([suggestion("high", (evidence(Channel.MUSICAL, 0.9),))])[0]
        ranked = rank([low, high], minimum=0.5)
        assert [item.id for item in ranked] == ["high"]

    def test_limit_is_applied_after_ordering(self) -> None:
        items = [
            apply_confidence([suggestion(f"s{index}", (evidence(Channel.MUSICAL, index / 10),))])[0]
            for index in range(1, 10)
        ]
        ranked = rank(items, limit=3)
        assert len(ranked) == 3
        assert ranked[0].confidence.calibrated >= ranked[-1].confidence.calibrated


def test_null_verifier_abstains() -> None:
    """Abstaining is a first-class outcome; returning a neutral 0.5 would poison every score."""
    verifier = NullVerifier()
    assert verifier.verify(VerificationRequest(suggestion())) is None
