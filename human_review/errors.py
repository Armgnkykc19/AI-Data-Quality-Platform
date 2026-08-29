from __future__ import annotations


class HumanReviewError(Exception):
    """Base error for human review domain failures."""


class ReviewCaseNotFoundError(HumanReviewError):
    """Raised when a review case identifier does not exist."""


class InvalidReviewTransitionError(HumanReviewError):
    """Raised when a resolution would violate the review state machine."""


class HumanReviewContradictionError(HumanReviewError):
    """Raised when a human MATCH would violate a human NO_MATCH constraint."""


class HumanReviewAuthorizationError(HumanReviewError):
    """Raised when a human MATCH would merge an unauthorized severe identity conflict."""


class HumanReviewAuthorizationContextError(HumanReviewError):
    """Raised when a human MATCH is requested without full authorization context."""


class HumanReviewReportError(HumanReviewError):
    """Raised when a persisted review report is missing, malformed, or unsupported."""
