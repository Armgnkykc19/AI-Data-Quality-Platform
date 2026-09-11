from __future__ import annotations

from semantic_review.models import SemanticFailureCode


class SemanticReviewError(Exception):
    """Base error for controlled LLM semantic-review failures."""

    failure_code: SemanticFailureCode = SemanticFailureCode.CONFIGURATION_ERROR

    def __init__(self, message: str, *, failure_code: SemanticFailureCode | None = None) -> None:
        super().__init__(message)
        if failure_code is not None:
            self.failure_code = failure_code


class SemanticReviewConfigurationError(SemanticReviewError):
    failure_code = SemanticFailureCode.CONFIGURATION_ERROR


class SemanticReviewRoutingError(SemanticReviewError):
    failure_code = SemanticFailureCode.UNSAFE_OR_INELIGIBLE_CASE


class SemanticReviewBudgetError(SemanticReviewError):
    failure_code = SemanticFailureCode.BUDGET_EXCEEDED


class SemanticReviewProviderError(SemanticReviewError):
    """Transient provider/infrastructure failure. May be retried."""

    failure_code = SemanticFailureCode.PROVIDER_UNAVAILABLE


class SemanticReviewTimeoutError(SemanticReviewProviderError):
    failure_code = SemanticFailureCode.PROVIDER_TIMEOUT


class SemanticReviewRateLimitError(SemanticReviewProviderError):
    failure_code = SemanticFailureCode.PROVIDER_RATE_LIMIT


class SemanticReviewClientError(SemanticReviewError):
    """Non-retryable provider client error (4xx other than 429)."""

    failure_code = SemanticFailureCode.PROVIDER_UNAVAILABLE


class SemanticReviewInvalidOutputError(SemanticReviewError):
    failure_code = SemanticFailureCode.INVALID_STRUCTURED_OUTPUT


class SemanticReviewIdentityMismatchError(SemanticReviewInvalidOutputError):
    failure_code = SemanticFailureCode.RESPONSE_ID_MISMATCH


class SemanticReviewInputTooLargeError(SemanticReviewError):
    failure_code = SemanticFailureCode.INPUT_TOO_LARGE
