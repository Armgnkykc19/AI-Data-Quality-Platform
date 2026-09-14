"""Deterministic Sprint 09 suggestions for persistence tests. No LLM, no network.

Every suggestion here is a real ``SemanticSuggestion`` produced by the offline
``FixedSuggestionProvider`` from a real ``SemanticReviewRequest``. Nothing is a
hand-built lookalike dict: the point of the Phase E tests is that persistence
accepts exactly what Sprint 09 produces and refuses everything else, which only
means something if the fixtures are genuine.

``SemanticReviewService`` is deliberately not used. It owns routing, budget and
retry, none of which persistence is testing, and Phase E must not depend on it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from human_review.models import ReviewCase
from semantic_review.config import SemanticReviewConfig, load_semantic_review_config
from semantic_review.ids import stable_suggestion_id
from semantic_review.models import (
    SemanticFailureCode,
    SemanticReviewRequest,
    SemanticSuggestion,
    SemanticSuggestionType,
)
from semantic_review.providers.fake_provider import (
    insufficient_evidence_provider,
    match_provider,
    no_match_provider,
)
from semantic_review.request_builder import build_semantic_review_request

SUGGESTED_AT = datetime(2026, 9, 12, 8, 30, 0, tzinfo=UTC)


def semantic_config() -> SemanticReviewConfig:
    return load_semantic_review_config()


def suggestion_request(
    case: ReviewCase,
    records_by_id: dict,
    *,
    config: SemanticReviewConfig | None = None,
    at: datetime = SUGGESTED_AT,
) -> SemanticReviewRequest:
    """Build the Sprint 09 request with a frozen clock.

    The request timestamp flows into the suggestion's ``created_at_utc``, so
    freezing it here is what makes a recorded suggestion byte-identical across
    runs -- which the content-addressed idempotency tests depend on.
    """
    return build_semantic_review_request(
        case,
        records_by_id,
        config=config or semantic_config(),
        clock=lambda: at,
    )


_PROVIDERS = {
    SemanticSuggestionType.SUGGEST_MATCH: match_provider,
    SemanticSuggestionType.SUGGEST_NO_MATCH: no_match_provider,
    SemanticSuggestionType.INSUFFICIENT_EVIDENCE: insufficient_evidence_provider,
}


def make_suggestion(
    case: ReviewCase,
    records_by_id: dict,
    *,
    suggestion: SemanticSuggestionType = SemanticSuggestionType.SUGGEST_MATCH,
    at: datetime = SUGGESTED_AT,
) -> SemanticSuggestion:
    """An advisory suggestion from the offline provider for the given case."""
    config = semantic_config()
    request = suggestion_request(case, records_by_id, config=config, at=at)
    provider = _PROVIDERS[suggestion](config.pricing, config.model)
    return provider.suggest(request)


def as_provider_failure(
    suggestion: SemanticSuggestion,
    *,
    failure_code: SemanticFailureCode = SemanticFailureCode.PROVIDER_TIMEOUT,
) -> SemanticSuggestion:
    """Turn a real suggestion into the PROVIDER_FAILURE form Sprint 09 emits.

    Mirrors ``semantic_review.service._failure_suggestion``: the advisory value
    becomes PROVIDER_FAILURE, the failure code becomes the single reason code,
    no model or provider request id came back, and the id is recomputed with
    Sprint 09's own ``stable_suggestion_id`` so it stays a true content address.

    The private service helper is not imported; what is reused is the public id
    function, which is the part that has to agree.
    """
    return replace(
        suggestion,
        suggestion=SemanticSuggestionType.PROVIDER_FAILURE,
        reason_codes=(failure_code.value,),
        explanation="Provider timed out.",
        returned_model=None,
        model_snapshot=None,
        provider_request_id=None,
        failure_code=failure_code,
        result_source="provider_failure_fail_closed",
        suggestion_id=stable_suggestion_id(
            request_id=suggestion.request_id,
            suggestion=SemanticSuggestionType.PROVIDER_FAILURE.value,
            attempt_count=suggestion.attempt_count,
            fingerprint=suggestion.request_fingerprint,
        ),
    )


def with_same_id_different_content(suggestion: SemanticSuggestion) -> SemanticSuggestion:
    """A second observation claiming the first one's content address.

    Only a measurement field changes, so the id -- derived from request,
    advisory value, attempt count and fingerprint -- stays valid. This is the
    narrow case where two genuinely different observations can collide, and it
    is what the conflict path has to catch.
    """
    return replace(suggestion, latency_ms=suggestion.latency_ms + 999)
