from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.semantic_review_benchmark import (
    FORBIDDEN_SEMANTIC_SPLITS,
    SemanticReviewBenchmarkResult,
    _ratio,
    run_semantic_review_benchmark,
)
from semantic_review.cost import estimate_cost_usd
from semantic_review.models import CostMode, SemanticSuggestionType
from semantic_review.providers.fake_provider import FixedSuggestionProvider, match_provider


def test_zero_denominator_is_undefined() -> None:
    assert _ratio(0, 0) is None
    assert _ratio(1, 0) is None
    assert _ratio(1, 2) == 0.5
    empty = SemanticReviewBenchmarkResult(
        split_name="validation",
        result_source="offline_fake_provider",
        live=False,
        benchmark_mode="OFFLINE_FAKE_SMOKE",
    )
    empty.suggest_match_precision = _ratio(
        empty.correct_match_suggestions,
        empty.correct_match_suggestions + empty.incorrect_match_suggestions,
    )
    assert empty.suggest_match_precision is None
    assert "undefined" in empty.to_text()


def test_incorrect_match_fails_precision() -> None:
    result = SemanticReviewBenchmarkResult(
        split_name="validation",
        result_source="offline_fake_provider",
        live=False,
        benchmark_mode="OFFLINE_FAKE_SMOKE",
        correct_match_suggestions=99,
        incorrect_match_suggestions=1,
    )
    result.suggest_match_precision = _ratio(99, 100)
    assert result.suggest_match_precision == 0.99


def test_cost_from_token_usage(semantic_config) -> None:
    cost = estimate_cost_usd(
        model=semantic_config.model,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        pricing=semantic_config.pricing,
    )
    assert cost == 1.40


def test_fake_provider_reports_simulated_zero_actual(review_bundle, semantic_config) -> None:
    from semantic_review.request_builder import build_semantic_review_request
    from tests.semantic_review.conftest import frozen_clock

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


def test_forbidden_splits() -> None:
    for split in FORBIDDEN_SEMANTIC_SPLITS:
        with pytest.raises(ValueError, match="forbids"):
            run_semantic_review_benchmark(
                dataset_path=Path("datasets/golden/v0.1.0"),
                split_name=split,
            )


def test_fake_and_scripted_cannot_claim_live_model_quality(semantic_config) -> None:
    fake = match_provider(semantic_config.pricing, semantic_config.model)
    assert fake.live is False
    live_like = FixedSuggestionProvider(
        suggestion=SemanticSuggestionType.INSUFFICIENT_EVIDENCE,
        pricing=semantic_config.pricing,
        requested_model=semantic_config.model,
    )
    assert live_like.live is False
    result = SemanticReviewBenchmarkResult(
        split_name="validation",
        result_source="offline_fake_provider",
        live=False,
        benchmark_mode="OFFLINE_FAKE_SMOKE",
        model_quality_claim=False,
        live_model_observation=False,
    )
    assert result.model_quality_claim is False
    assert result.live_model_observation is False
