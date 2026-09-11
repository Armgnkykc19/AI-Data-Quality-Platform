from __future__ import annotations

import os

import pytest

from semantic_review.budget import LiveBudget
from semantic_review.errors import (
    SemanticReviewBudgetError,
    SemanticReviewConfigurationError,
    SemanticReviewIdentityMismatchError,
    SemanticReviewInvalidOutputError,
    SemanticReviewTimeoutError,
)
from semantic_review.models import SemanticSuggestionType
from semantic_review.providers.fake_provider import (
    FixedSuggestionProvider,
    generic_failure_provider,
    insufficient_evidence_provider,
    invalid_schema_provider,
    match_provider,
    mismatched_case_provider,
    mismatched_record_a_provider,
    mismatched_record_b_provider,
    no_match_provider,
    rate_limit_provider,
    timeout_provider,
)
from semantic_review.providers.openai_provider import API_KEY_ENV, OpenAIProvider
from semantic_review.request_builder import build_semantic_review_request
from semantic_review.schema import parse_provider_payload
from semantic_review.service import SemanticReviewService
from tests.semantic_review.conftest import frozen_clock


def _request(review_bundle, semantic_config):
    _resolution, state, records_by_id, _er = review_bundle
    return build_semantic_review_request(
        state.cases[0],
        records_by_id,
        config=semantic_config,
        clock=frozen_clock,
    )


@pytest.mark.parametrize(
    "factory, expected",
    [
        (match_provider, SemanticSuggestionType.SUGGEST_MATCH),
        (no_match_provider, SemanticSuggestionType.SUGGEST_NO_MATCH),
        (insufficient_evidence_provider, SemanticSuggestionType.INSUFFICIENT_EVIDENCE),
    ],
)
def test_allowed_suggestion_enums(review_bundle, semantic_config, factory, expected) -> None:
    provider = factory(semantic_config.pricing, semantic_config.model)
    suggestion = provider.suggest(_request(review_bundle, semantic_config))
    assert suggestion.suggestion == expected
    assert suggestion.live is False


def test_invalid_schema_and_malformed_json(review_bundle, semantic_config) -> None:
    provider = invalid_schema_provider(semantic_config.pricing, semantic_config.model)
    with pytest.raises(SemanticReviewInvalidOutputError):
        provider.suggest(_request(review_bundle, semantic_config))
    with pytest.raises(SemanticReviewInvalidOutputError, match="malformed"):
        parse_provider_payload(
            "not-json",
            expected_review_case_id="RC-1",
            expected_record_a_id="a",
            expected_record_b_id="b",
        )


def test_identity_mismatches_fail_closed(review_bundle, semantic_config) -> None:
    request = _request(review_bundle, semantic_config)
    with pytest.raises(SemanticReviewIdentityMismatchError):
        mismatched_case_provider(semantic_config.pricing, semantic_config.model).suggest(request)
    with pytest.raises(SemanticReviewIdentityMismatchError):
        mismatched_record_a_provider(semantic_config.pricing, semantic_config.model).suggest(
            request
        )
    with pytest.raises(SemanticReviewIdentityMismatchError):
        mismatched_record_b_provider(semantic_config.pricing, semantic_config.model).suggest(
            request
        )


def test_timeout_is_retried_then_identity_errors_are_not(review_bundle, semantic_config) -> None:
    _resolution, state, records_by_id, er_config = review_bundle
    retrying = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.SUGGEST_NO_MATCH,
        pricing=semantic_config.pricing,
        requested_model=semantic_config.model,
        fail_times=1,
        failure=SemanticReviewTimeoutError("once"),
    )
    service = SemanticReviewService(semantic_config, retrying, require_enabled=False)
    _routing, suggestion = service.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    assert suggestion is not None
    assert suggestion.suggestion == SemanticSuggestionType.SUGGEST_NO_MATCH
    assert suggestion.attempt_count == 2
    assert retrying.calls == 2

    identity = mismatched_case_provider(semantic_config.pricing, semantic_config.model)
    limited = SemanticReviewService(semantic_config, identity, require_enabled=False)
    _routing, failed = limited.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    assert failed is not None
    assert failed.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert identity.calls == 1


def test_invalid_output_is_not_retried(review_bundle, semantic_config) -> None:
    _resolution, state, records_by_id, er_config = review_bundle
    provider = generic_failure_provider(semantic_config.pricing, semantic_config.model)
    service = SemanticReviewService(semantic_config, provider, require_enabled=False)
    _routing, failed = service.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    assert failed is not None
    assert failed.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert provider.calls == 1


def test_timeout_and_rate_limit_exhaustion_become_provider_failure(
    review_bundle, semantic_config
) -> None:
    _resolution, state, records_by_id, er_config = review_bundle
    always_fail = timeout_provider(semantic_config.pricing, semantic_config.model)
    limited = SemanticReviewService(semantic_config, always_fail, require_enabled=False)
    _routing, failed = limited.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    assert failed is not None
    assert failed.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert always_fail.calls == semantic_config.max_retries + 1

    rate = rate_limit_provider(semantic_config.pricing, semantic_config.model)
    limited_rate = SemanticReviewService(semantic_config, rate, require_enabled=False)
    _routing, failed_rate = limited_rate.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    assert failed_rate is not None
    assert failed_rate.suggestion == SemanticSuggestionType.PROVIDER_FAILURE
    assert rate.calls == semantic_config.max_retries + 1


def test_missing_api_key_fail_closed(review_bundle, enabled_semantic_config, monkeypatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    provider = OpenAIProvider(enabled_semantic_config, client=None)
    request = _request(review_bundle, enabled_semantic_config)
    with pytest.raises(SemanticReviewConfigurationError, match="OPENAI_API_KEY"):
        provider.suggest(request)
    assert provider.calls == 1


def test_openai_adapter_uses_injected_client(review_bundle, enabled_semantic_config) -> None:
    request = _request(review_bundle, enabled_semantic_config)

    class _Usage:
        input_tokens = 11
        output_tokens = 7

    class _Response:
        output_text = (
            '{"review_case_id": "'
            + request.review_case_id
            + '", "record_a_id": "'
            + request.record_a_id
            + '", "record_b_id": "'
            + request.record_b_id
            + '", "suggestion": "INSUFFICIENT_EVIDENCE", '
            '"reason_codes": ["WEAK_EVIDENCE"], "explanation": "Not enough evidence."}'
        )
        usage = _Usage()
        model = enabled_semantic_config.model
        id = "resp_test_1"

    class _Responses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return _Response()

    class _Client:
        def __init__(self) -> None:
            self.responses = _Responses()

    client = _Client()
    provider = OpenAIProvider(enabled_semantic_config, client=client)
    suggestion = provider.suggest(request)
    assert suggestion.suggestion == SemanticSuggestionType.INSUFFICIENT_EVIDENCE
    assert client.responses.kwargs["model"] == enabled_semantic_config.model
    assert client.responses.kwargs["reasoning"] == {
        "effort": enabled_semantic_config.reasoning_effort
    }
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["tools"] == []
    assert client.responses.kwargs["max_output_tokens"] == enabled_semantic_config.max_output_tokens
    assert enabled_semantic_config.max_output_tokens == 600
    assert "temperature" not in client.responses.kwargs
    assert client.responses.kwargs["text"]["format"]["strict"] is True


def test_live_budget_enforces_calls_and_usd(review_bundle, semantic_config) -> None:
    from dataclasses import replace

    _resolution, state, records_by_id, er_config = review_bundle
    tight = replace(semantic_config, max_live_calls=1, max_live_usd=0.00000001)
    live_provider = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=tight.pricing,
        requested_model=tight.model,
        live=True,
        input_token_count=1,
        output_token_count=1,
    )
    budget = LiveBudget(tight)
    service = SemanticReviewService(tight, live_provider, require_enabled=False, budget=budget)
    with pytest.raises(SemanticReviewBudgetError, match="USD"):
        service.suggest_for_case(
            state.cases[0],
            records_by_id=records_by_id,
            resolution=_resolution,
            entity_resolution_config=er_config,
        )
    assert live_provider.calls == 0

    call_capped = replace(semantic_config, max_live_calls=1, max_live_usd=10.0)
    live_ok = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=call_capped.pricing,
        requested_model=call_capped.model,
        live=True,
        input_token_count=1,
        output_token_count=1,
    )
    budget_ok = LiveBudget(call_capped)
    first = SemanticReviewService(call_capped, live_ok, require_enabled=False, budget=budget_ok)
    first.suggest_for_case(
        state.cases[0],
        records_by_id=records_by_id,
        resolution=_resolution,
        entity_resolution_config=er_config,
    )
    with pytest.raises(SemanticReviewBudgetError, match="call cap"):
        first.suggest_for_case(
            state.cases[0],
            records_by_id=records_by_id,
            resolution=_resolution,
            entity_resolution_config=er_config,
        )
    assert live_ok.calls == 1


def test_live_sdk_factory_is_blocked_during_pytest() -> None:
    from semantic_review.providers import openai_provider

    with pytest.raises(AssertionError, match="forbidden"):
        openai_provider.create_openai_sdk_client(api_key="sk-test", timeout=1)


def test_default_suite_does_not_set_api_key() -> None:
    assert not os.environ.get(API_KEY_ENV)


def test_ci_workflow_does_not_supply_openai_key() -> None:
    from pathlib import Path

    workflow = Path(".github/workflows/evaluation-ci.yml").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in workflow
    assert "llm-openai" not in workflow
