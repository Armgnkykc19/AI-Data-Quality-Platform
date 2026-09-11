from __future__ import annotations

from dataclasses import replace

import pytest

from human_review.cases import generate_review_cases
from human_review.workflow import ReviewWorkflow
from semantic_review.budget import LiveBudget
from semantic_review.cost import conservative_call_cost_usd
from semantic_review.errors import (
    SemanticReviewBudgetError,
    SemanticReviewClientError,
    SemanticReviewConfigurationError,
    SemanticReviewIdentityMismatchError,
    SemanticReviewInvalidOutputError,
    SemanticReviewProviderError,
    SemanticReviewRateLimitError,
    SemanticReviewTimeoutError,
)
from semantic_review.models import SemanticSuggestionType
from semantic_review.providers.fake_provider import (
    FixedSuggestionProvider,
    insufficient_evidence_provider,
    match_provider,
    mismatched_case_provider,
    timeout_provider,
)
from semantic_review.providers.openai_provider import _map_openai_error
from semantic_review.schema import parse_provider_payload
from semantic_review.service import SemanticReviewService
from tests.human_review.conftest import make_chain_review_resolution


def _status_error(name: str, status: int):
    exc_type = type(name, (Exception,), {})
    exc = exc_type(f"{name} {status}")
    exc.status_code = status
    return exc


def _suggest(service, review_bundle):
    resolution, state, records_by_id, er_config = review_bundle
    return service.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=resolution,
        entity_resolution_config=er_config,
    )


def test_retry_consumes_live_call_budget(review_bundle, semantic_config) -> None:
    live_config = replace(semantic_config, max_live_calls=2, max_live_usd=10.0)
    provider = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=live_config.pricing,
        requested_model=live_config.model,
        live=True,
        fail_times=1,
        failure=SemanticReviewTimeoutError("once"),
    )
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _routing, suggestion = _suggest(service, review_bundle)
    assert suggestion is not None
    assert suggestion.suggestion == SemanticSuggestionType.INSUFFICIENT_EVIDENCE
    assert provider.calls == 2
    assert budget.calls == 2


def test_max_calls_one_blocks_retry(review_bundle, semantic_config) -> None:
    live_config = replace(semantic_config, max_live_calls=1, max_live_usd=10.0, max_retries=1)
    provider = timeout_provider(live_config.pricing, live_config.model)
    provider.live = True
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _routing, suggestion = _suggest(service, review_bundle)
    assert suggestion is not None
    assert suggestion.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert provider.calls == 1
    assert budget.calls == 1


def test_max_calls_two_allows_exactly_two_invocations(review_bundle, semantic_config) -> None:
    live_config = replace(semantic_config, max_live_calls=2, max_live_usd=10.0, max_retries=5)
    provider = timeout_provider(live_config.pricing, live_config.model)
    provider.live = True
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _suggest(service, review_bundle)
    assert provider.calls == 2
    assert budget.calls == 2


def test_identity_failure_consumes_one_live_call_and_is_not_retried(
    review_bundle, semantic_config
) -> None:
    live_config = replace(semantic_config, max_live_calls=3, max_live_usd=10.0)
    provider = mismatched_case_provider(live_config.pricing, live_config.model)
    provider.live = True
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _routing, suggestion = _suggest(service, review_bundle)
    assert suggestion is not None
    assert suggestion.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert provider.calls == 1
    assert budget.calls == 1


def test_offline_fake_does_not_consume_live_budget(review_bundle, semantic_config) -> None:
    budget = LiveBudget(replace(semantic_config, max_live_calls=1, max_live_usd=10.0))
    provider = match_provider(semantic_config.pricing, semantic_config.model)
    service = SemanticReviewService(semantic_config, provider, require_enabled=False, budget=budget)
    _suggest(service, review_bundle)
    assert provider.live is False
    assert budget.calls == 0
    assert budget.spent_usd == 0.0


def test_usd_reservation_refuses_before_provider_call(review_bundle, semantic_config) -> None:
    reserved = conservative_call_cost_usd(
        semantic_config.model,
        semantic_config.pricing,
        max_input_tokens=semantic_config.max_input_tokens,
        max_output_tokens=semantic_config.max_output_tokens,
    )
    tight = replace(semantic_config, max_live_calls=10, max_live_usd=reserved / 2)
    provider = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=tight.pricing,
        requested_model=tight.model,
        live=True,
    )
    budget = LiveBudget(tight)
    service = SemanticReviewService(tight, provider, require_enabled=False, budget=budget)
    with pytest.raises(SemanticReviewBudgetError, match="USD"):
        _suggest(service, review_bundle)
    assert provider.calls == 0
    assert budget.calls == 0


def test_usd_exact_boundary_allows_one_reserved_call(review_bundle, semantic_config) -> None:
    reserved = conservative_call_cost_usd(
        semantic_config.model,
        semantic_config.pricing,
        max_input_tokens=semantic_config.max_input_tokens,
        max_output_tokens=semantic_config.max_output_tokens,
    )
    exact = replace(semantic_config, max_live_calls=5, max_live_usd=reserved)
    provider = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=exact.pricing,
        requested_model=exact.model,
        live=True,
        input_token_count=1,
        output_token_count=1,
    )
    budget = LiveBudget(exact)
    service = SemanticReviewService(exact, provider, require_enabled=False, budget=budget)
    _suggest(service, review_bundle)
    assert provider.calls == 1
    with pytest.raises(SemanticReviewBudgetError):
        _suggest(service, review_bundle)
    assert provider.calls == 1


def test_failed_attempt_with_unknown_usage_is_not_free(review_bundle, semantic_config) -> None:
    reserved = conservative_call_cost_usd(
        semantic_config.model,
        semantic_config.pricing,
        max_input_tokens=semantic_config.max_input_tokens,
        max_output_tokens=semantic_config.max_output_tokens,
    )
    live_config = replace(semantic_config, max_live_calls=5, max_live_usd=reserved * 1.5)
    provider = timeout_provider(live_config.pricing, live_config.model)
    provider.live = True
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _suggest(service, review_bundle)
    assert budget.calls == 1
    assert budget.spent_usd == reserved
    with pytest.raises(SemanticReviewBudgetError, match="USD"):
        _suggest(service, review_bundle)
    assert provider.calls == 1


def test_shared_budget_covers_multiple_cases(resolution_config, semantic_config) -> None:
    live_config = replace(semantic_config, max_live_calls=1, max_live_usd=10.0)
    resolution = make_chain_review_resolution(("c1", "c2", "c3"))
    state = generate_review_cases(resolution, config=resolution_config)
    records_by_id = {record.record_id: record for record in resolution.records}
    workflow = ReviewWorkflow(state)
    provider = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=live_config.pricing,
        requested_model=live_config.model,
        live=True,
        input_token_count=1,
        output_token_count=1,
    )
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    first = service.suggest_for_case(
        workflow.state.cases[0],
        records_by_id=records_by_id,
        resolution=resolution,
        entity_resolution_config=resolution_config,
    )
    assert first[1] is not None
    with pytest.raises(SemanticReviewBudgetError, match="call cap"):
        service.suggest_for_case(
            workflow.state.cases[1],
            records_by_id=records_by_id,
            resolution=resolution,
            entity_resolution_config=resolution_config,
        )
    assert provider.calls == 1


def test_reservation_uses_uncached_not_cached_rates(semantic_config) -> None:
    reserved = conservative_call_cost_usd(
        semantic_config.model,
        semantic_config.pricing,
        max_input_tokens=semantic_config.max_input_tokens,
        max_output_tokens=semantic_config.max_output_tokens,
    )
    from semantic_review.cost import estimate_cost_usd

    cached = estimate_cost_usd(
        model=semantic_config.model,
        input_tokens=semantic_config.max_input_tokens,
        cached_input_tokens=semantic_config.max_input_tokens,
        output_tokens=semantic_config.max_output_tokens,
        pricing=semantic_config.pricing,
    )
    assert reserved > cached
    assert reserved == estimate_cost_usd(
        model=semantic_config.model,
        input_tokens=semantic_config.max_input_tokens,
        cached_input_tokens=0,
        output_tokens=semantic_config.max_output_tokens,
        pricing=semantic_config.pricing,
    )


def test_usd_budget_blocks_retry_after_reserved_failure(review_bundle, semantic_config) -> None:
    reserved = conservative_call_cost_usd(
        semantic_config.model,
        semantic_config.pricing,
        max_input_tokens=semantic_config.max_input_tokens,
        max_output_tokens=semantic_config.max_output_tokens,
    )
    live_config = replace(
        semantic_config, max_live_calls=5, max_live_usd=reserved * 1.1, max_retries=3
    )
    provider = timeout_provider(live_config.pricing, live_config.model)
    provider.live = True
    budget = LiveBudget(live_config)
    service = SemanticReviewService(live_config, provider, require_enabled=False, budget=budget)
    _routing, suggestion = _suggest(service, review_bundle)
    assert suggestion is not None
    assert suggestion.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert provider.calls == 1
    assert budget.calls == 1
    assert budget.spent_usd == reserved


def test_unknown_pricing_fails_closed_for_budget(semantic_config) -> None:
    from semantic_review.errors import SemanticReviewConfigurationError as ConfigError

    bad = replace(semantic_config, model="unknown-model")
    with pytest.raises(ConfigError, match="must not inherit"):
        LiveBudget(bad)


@pytest.mark.parametrize(
    ("exc", "expected_type", "retry"),
    [
        (TimeoutError("timed out"), SemanticReviewTimeoutError, True),
        (ConnectionError("network down"), SemanticReviewProviderError, True),
        (_status_error("RateLimitError", 429), SemanticReviewRateLimitError, True),
        (_status_error("APIStatusError", 500), SemanticReviewProviderError, True),
        (_status_error("APIStatusError", 503), SemanticReviewProviderError, True),
        (_status_error("APIStatusError", 400), SemanticReviewClientError, False),
        (_status_error("AuthenticationError", 401), SemanticReviewConfigurationError, False),
        (_status_error("PermissionDeniedError", 403), SemanticReviewConfigurationError, False),
    ],
)
def test_openai_error_retry_classification(exc, expected_type, retry) -> None:
    mapped = _map_openai_error(exc)
    assert isinstance(mapped, expected_type)
    from semantic_review.service import RETRYABLE

    assert isinstance(mapped, RETRYABLE) is retry


def test_schema_and_identity_errors_are_not_retryable() -> None:
    from semantic_review.service import RETRYABLE

    assert not isinstance(SemanticReviewInvalidOutputError("bad"), RETRYABLE)
    assert not isinstance(SemanticReviewIdentityMismatchError("id"), RETRYABLE)
    assert not isinstance(SemanticReviewClientError("400"), RETRYABLE)
    assert not isinstance(SemanticReviewConfigurationError("OPENAI_API_KEY is missing"), RETRYABLE)
    assert isinstance(SemanticReviewProviderError("transient"), RETRYABLE)
    assert not isinstance(SemanticReviewProviderError("transient"), SemanticReviewClientError)


def test_malformed_and_match_token_are_not_retryable() -> None:
    from semantic_review.service import RETRYABLE

    with pytest.raises(SemanticReviewInvalidOutputError) as malformed:
        parse_provider_payload(
            "{",
            expected_review_case_id="RC-1",
            expected_record_a_id="a",
            expected_record_b_id="b",
        )
    assert not isinstance(malformed.value, RETRYABLE)
    with pytest.raises(SemanticReviewInvalidOutputError) as match_token:
        parse_provider_payload(
            {
                "review_case_id": "RC-1",
                "record_a_id": "a",
                "record_b_id": "b",
                "suggestion": "MATCH",
                "reason_codes": ["X"],
                "explanation": "no",
            },
            expected_review_case_id="RC-1",
            expected_record_a_id="a",
            expected_record_b_id="b",
        )
    assert not isinstance(match_token.value, RETRYABLE)


def test_fake_provider_never_constructs_openai_sdk(review_bundle, semantic_config) -> None:
    from semantic_review.providers import openai_provider

    _suggest(
        SemanticReviewService(
            semantic_config,
            insufficient_evidence_provider(semantic_config.pricing, semantic_config.model),
            require_enabled=False,
        ),
        review_bundle,
    )
    with pytest.raises(AssertionError, match="forbidden"):
        openai_provider.create_openai_sdk_client(api_key="sk-test", timeout=1)
