from __future__ import annotations

import pytest

from semantic_review.config import load_semantic_review_pricing
from semantic_review.cost import (
    TokenUsage,
    estimate_cost,
    estimate_cost_usd,
    parse_provider_usage,
)
from semantic_review.errors import SemanticReviewConfigurationError
from semantic_review.models import CostMode
from semantic_review.providers.fake_provider import match_provider
from semantic_review.request_builder import build_semantic_review_request
from tests.semantic_review.conftest import frozen_clock


def test_official_luna_rates_are_not_assumed() -> None:
    pricing = load_semantic_review_pricing()
    assert "assumed" not in pricing.version.lower()
    assert pricing.provider == "openai"
    assert pricing.model == "gpt-5.6-luna"
    assert pricing.currency == "USD"
    assert pricing.unit == "per_million_tokens"
    assert pricing.pricing_type == "standard"
    assert pricing.source_url == "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
    assert pricing.verified_date == "2026-08-28"
    rates = pricing.models["gpt-5.6-luna"]
    assert rates.input_usd_per_million == 0.20
    assert rates.cached_input_usd_per_million == 0.02
    assert rates.output_usd_per_million == 1.20


def test_exact_uncached_input_output_and_cached_costs() -> None:
    pricing = load_semantic_review_pricing()
    model = "gpt-5.6-luna"
    assert (
        estimate_cost_usd(
            model=model,
            input_tokens=1_000_000,
            output_tokens=0,
            pricing=pricing,
        )
        == 0.20
    )
    assert (
        estimate_cost_usd(
            model=model,
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
            output_tokens=0,
            pricing=pricing,
        )
        == 0.02
    )
    assert (
        estimate_cost_usd(
            model=model,
            input_tokens=0,
            output_tokens=1_000_000,
            pricing=pricing,
        )
        == 1.20
    )
    mixed = estimate_cost_usd(
        model=model,
        input_tokens=1_000_000,
        cached_input_tokens=500_000,
        output_tokens=1_000_000,
        pricing=pricing,
    )
    assert mixed == pytest.approx(0.10 + 0.01 + 1.20)


def test_reasoning_tokens_are_not_double_counted_when_included_in_output() -> None:
    pricing = load_semantic_review_pricing()
    cost = estimate_cost(
        model="gpt-5.6-luna",
        pricing=pricing,
        live=True,
        usage=TokenUsage(
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=1_000_000,
            reasoning_tokens=800_000,
            available=True,
        ),
    )
    assert cost.actual_provider_cost_usd == 1.20
    assert cost.reasoning_tokens == 800_000


def test_reasoning_tokens_used_when_output_missing() -> None:
    pricing = load_semantic_review_pricing()
    cost = estimate_cost(
        model="gpt-5.6-luna",
        pricing=pricing,
        live=True,
        usage=TokenUsage(
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=None,
            reasoning_tokens=1_000_000,
            available=True,
        ),
    )
    assert cost.actual_provider_cost_usd == 1.20


def test_zero_token_usage_is_zero_cost() -> None:
    pricing = load_semantic_review_pricing()
    cost = estimate_cost(
        model="gpt-5.6-luna",
        pricing=pricing,
        live=True,
        usage=TokenUsage(0, 0, 0, 0, available=True),
    )
    assert cost.actual_provider_cost_usd == 0.0
    assert cost.hypothetical_model_cost_usd == 0.0


def test_fake_provider_cost_is_simulated_zero_actual(review_bundle, semantic_config) -> None:
    _resolution, state, records_by_id, _er = review_bundle
    request = build_semantic_review_request(
        state.cases[0],
        records_by_id,
        config=semantic_config,
        clock=frozen_clock,
    )
    suggestion = match_provider(semantic_config.pricing, semantic_config.model).suggest(request)
    assert suggestion.cost_mode == CostMode.SIMULATED
    assert suggestion.actual_provider_cost_usd == 0.0
    assert suggestion.hypothetical_model_cost_usd is not None
    assert suggestion.hypothetical_model_cost_usd > 0.0
    assert suggestion.pricing_version == semantic_config.pricing.version


def test_missing_usage_does_not_invent_a_bill() -> None:
    pricing = load_semantic_review_pricing()
    missing_live = estimate_cost(
        model="gpt-5.6-luna",
        pricing=pricing,
        live=True,
        usage=TokenUsage(None, None, None, None, available=False),
    )
    assert missing_live.usage_available is False
    assert missing_live.actual_provider_cost_usd is None
    assert missing_live.hypothetical_model_cost_usd is None


def test_unknown_model_does_not_inherit_luna_rates() -> None:
    pricing = load_semantic_review_pricing()
    with pytest.raises(SemanticReviewConfigurationError, match="must not inherit"):
        estimate_cost_usd(
            model="gpt-4o",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            pricing=pricing,
        )


def test_parse_provider_usage_reads_cached_and_reasoning() -> None:
    class _Cached:
        cached_tokens = 20

    class _Reasoning:
        reasoning_tokens = 8

    class _Usage:
        input_tokens = 100
        output_tokens = 30
        input_tokens_details = _Cached()
        output_tokens_details = _Reasoning()

    usage = parse_provider_usage(_Usage())
    assert usage.available is True
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 20
    assert usage.output_tokens == 30
    assert usage.reasoning_tokens == 8
    assert parse_provider_usage(None).available is False
