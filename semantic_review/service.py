from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import EntityRecord, ResolutionResult
from human_review.models import HumanReviewOutcome, ReviewCase
from semantic_review.audit import write_suggestion_audit
from semantic_review.budget import LiveBudget
from semantic_review.config import SemanticReviewConfig
from semantic_review.cost import TokenUsage, suggestion_cost_fields
from semantic_review.errors import (
    SemanticReviewBudgetError,
    SemanticReviewError,
    SemanticReviewProviderError,
    SemanticReviewRateLimitError,
    SemanticReviewRoutingError,
    SemanticReviewTimeoutError,
)
from semantic_review.ids import stable_suggestion_id
from semantic_review.models import (
    SemanticFailureCode,
    SemanticRoutingDecision,
    SemanticSuggestion,
    SemanticSuggestionType,
)
from semantic_review.policy import evaluate_routing
from semantic_review.provider import SemanticReviewProvider
from semantic_review.request_builder import build_semantic_review_request

# Transient only. Timeout/RateLimit inherit ProviderError; listed for clarity.
# Client, configuration, schema, and identity errors are not in this set.
RETRYABLE = (
    SemanticReviewTimeoutError,
    SemanticReviewRateLimitError,
    SemanticReviewProviderError,
)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


class SemanticReviewService:
    def __init__(
        self,
        config: SemanticReviewConfig,
        provider: SemanticReviewProvider,
        *,
        clock: Clock = _now,
        require_enabled: bool | None = None,
        budget: LiveBudget | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._clock = clock
        if require_enabled is None:
            self._require_enabled = provider.live
        else:
            self._require_enabled = require_enabled
        self._budget = budget

    def suggest_for_case(
        self,
        case: ReviewCase | None,
        *,
        records_by_id: dict[str, EntityRecord],
        outcome: HumanReviewOutcome | None = None,
        resolution: ResolutionResult | None = None,
        entity_resolution_config: EntityResolutionConfig | None = None,
    ) -> tuple[SemanticRoutingDecision, SemanticSuggestion | None]:
        routing = evaluate_routing(
            case,
            config=self._config,
            outcome=outcome,
            resolution=resolution,
            records_by_id=records_by_id,
            entity_resolution_config=entity_resolution_config,
            require_enabled=self._require_enabled,
        )
        if not routing.eligible or case is None:
            return routing, None

        request = build_semantic_review_request(
            case,
            records_by_id,
            config=self._config,
            clock=self._clock,
        )

        budget: LiveBudget | None = None
        if self._provider.live:
            budget = self._budget if self._budget is not None else LiveBudget(self._config)
            self._budget = budget

        last_error: SemanticReviewError | None = None
        max_attempts = self._config.max_retries + 1
        suggestion: SemanticSuggestion | None = None
        for attempt in range(1, max_attempts + 1):
            if budget is not None:
                try:
                    budget.assert_can_call()
                except SemanticReviewBudgetError:
                    if last_error is not None:
                        suggestion = _failure_suggestion(
                            request,
                            config=self._config,
                            provider_name=self._provider.provider_name,
                            live=self._provider.live,
                            attempt_count=attempt - 1,
                            error=last_error,
                            created_at_utc=request.created_at_utc,
                        )
                        break
                    raise
            started = time.perf_counter()
            try:
                suggestion = self._provider.suggest(request)
                if budget is not None:
                    budget.consume_attempt(suggestion.actual_provider_cost_usd)
                latency_ms = int((time.perf_counter() - started) * 1000)
                suggestion = _with_attempts(
                    suggestion, attempt_count=attempt, latency_ms=latency_ms
                )
                last_error = None
                break
            except RETRYABLE as exc:
                last_error = exc
                if budget is not None:
                    budget.consume_attempt(None)
                if attempt >= max_attempts:
                    suggestion = _failure_suggestion(
                        request,
                        config=self._config,
                        provider_name=self._provider.provider_name,
                        live=self._provider.live,
                        attempt_count=attempt,
                        error=exc,
                        created_at_utc=request.created_at_utc,
                    )
                    break
            except SemanticReviewError as exc:
                last_error = exc
                if budget is not None:
                    budget.consume_attempt(None)
                suggestion = _failure_suggestion(
                    request,
                    config=self._config,
                    provider_name=self._provider.provider_name,
                    live=self._provider.live,
                    attempt_count=attempt,
                    error=exc,
                    created_at_utc=request.created_at_utc,
                )
                break
        if last_error is not None and suggestion is None:
            suggestion = _failure_suggestion(
                request,
                config=self._config,
                provider_name=self._provider.provider_name,
                live=self._provider.live,
                attempt_count=max_attempts,
                error=last_error,
                created_at_utc=request.created_at_utc,
            )

        if suggestion is None:
            raise SemanticReviewRoutingError("Provider returned no suggestion.")
        write_suggestion_audit(suggestion, output_directory=self._config.report_output_directory)
        return routing, suggestion


def require_eligible(routing: SemanticRoutingDecision) -> None:
    if not routing.eligible:
        raise SemanticReviewRoutingError(
            routing.detail, failure_code=SemanticFailureCode.UNSAFE_OR_INELIGIBLE_CASE
        )


def _with_attempts(
    suggestion: SemanticSuggestion, *, attempt_count: int, latency_ms: int
) -> SemanticSuggestion:
    values = suggestion.__dict__.copy()
    values["attempt_count"] = attempt_count
    values["latency_ms"] = latency_ms
    values["suggestion_id"] = stable_suggestion_id(
        request_id=suggestion.request_id,
        suggestion=suggestion.suggestion.value,
        attempt_count=attempt_count,
        fingerprint=suggestion.request_fingerprint,
    )
    return SemanticSuggestion(**values)


def _failure_suggestion(
    request,
    *,
    config: SemanticReviewConfig,
    provider_name: str,
    live: bool,
    attempt_count: int,
    error: SemanticReviewError,
    created_at_utc: str,
) -> SemanticSuggestion:
    failure_code = getattr(error, "failure_code", SemanticFailureCode.PROVIDER_UNAVAILABLE)
    return SemanticSuggestion(
        suggestion_id=stable_suggestion_id(
            request_id=request.request_id,
            suggestion=SemanticSuggestionType.PROVIDER_FAILURE.value,
            attempt_count=attempt_count,
            fingerprint=request.request_fingerprint,
        ),
        request_id=request.request_id,
        review_case_id=request.review_case_id,
        record_a_id=request.record_a_id,
        record_b_id=request.record_b_id,
        suggestion=SemanticSuggestionType.PROVIDER_FAILURE,
        reason_codes=(failure_code.value,),
        explanation=str(error)[:400],
        provider=provider_name,
        requested_model=config.model,
        returned_model=None,
        model_snapshot=None,
        provider_request_id=None,
        prompt_version=request.prompt_version,
        prompt_template_hash=request.prompt_template_hash,
        request_schema_version=request.request_schema_version,
        response_schema_version=request.response_schema_version,
        request_fingerprint=request.request_fingerprint,
        attempt_count=attempt_count,
        latency_ms=0,
        **suggestion_cost_fields(
            model=config.model,
            pricing=config.pricing,
            live=live,
            usage=TokenUsage(
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                available=False,
            ),
        ),
        pricing_version=config.pricing.version,
        failure_code=failure_code,
        created_at_utc=created_at_utc,
        live=live,
        result_source="provider_failure_fail_closed",
    )
