"""The verification-channel protocol.

Phase 1 ships musical and pattern evidence. Phases 2 and 3 add computer-vision and multimodal-AI
verification. Rather than bolting those on later, the interface they will implement is defined
now, and the fusion layer is already written against it — so adding a channel means writing a
class, not rewiring the pipeline.

The contract is intentionally minimal: given a suggestion, either return evidence or say you had
nothing to contribute. A verifier that cannot localize a note on the page (no scan, failed
alignment, low-confidence region) returns ``None``, and fusion renormalizes without it. It must
never return a neutral 0.5, which would silently drag every confidence toward the middle.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Evidence, Suggestion

__all__ = ["NullVerifier", "VerificationRequest", "Verifier"]


class VerificationRequest:
    """Everything a verifier needs about one suggestion.

    Carries the suggestion plus an optional handle to whatever source material the verifier
    consumes — a rasterized page for CV, a crop plus context for the AI adjudicator.
    """

    __slots__ = ("payload", "suggestion")

    def __init__(self, suggestion: Suggestion, payload: object | None = None) -> None:
        self.suggestion = suggestion
        self.payload = payload


@runtime_checkable
class Verifier(Protocol):
    """A source of independent evidence about whether a suggestion is right."""

    name: str

    def verify(self, request: VerificationRequest) -> Evidence | None:
        """Return evidence, or ``None`` when this channel has nothing to say.

        Returning ``None`` is a first-class outcome, not an error: it is how a verifier reports
        that it could not see the thing it was asked about.
        """
        ...


class NullVerifier:
    """A verifier that always abstains.

    Used as the default so the pipeline has no special case for "no verification configured",
    and as the control in tests that check the renormalization behaviour.
    """

    name = "null"

    def verify(self, request: VerificationRequest) -> Evidence | None:
        return None
