"""Turning evidence into a number the user can trust."""

from .channels import NullVerifier, VerificationRequest, Verifier
from .fusion import (
    apply_confidence,
    calibrate,
    channel_scores,
    fuse_evidence,
    merge_duplicates,
    rank,
    split_evidence,
)

__all__ = [
    "NullVerifier",
    "VerificationRequest",
    "Verifier",
    "apply_confidence",
    "calibrate",
    "channel_scores",
    "fuse_evidence",
    "merge_duplicates",
    "rank",
    "split_evidence",
]
