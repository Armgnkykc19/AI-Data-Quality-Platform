from __future__ import annotations

from evaluation.semantic_review_benchmark import (
    BENCHMARK_MODE_OFFLINE_SCRIPTED,
    select_stratified_labelled_sample,
)
from evaluation.semantic_review_scripted import run_scripted_offline_benchmark
from semantic_review.models import CostMode, SemanticSuggestionType


class _Case:
    def __init__(self, review_case_id: str) -> None:
        self.review_case_id = review_case_id


def test_scripted_benchmark_covers_formula_and_safety_paths() -> None:
    result = run_scripted_offline_benchmark()
    assert result.ran_successfully
    assert result.benchmark_mode == BENCHMARK_MODE_OFFLINE_SCRIPTED
    assert result.model_quality_claim is False
    assert result.live_model_observation is False
    assert result.cost_mode == CostMode.SIMULATED.value
    assert result.actual_provider_cost_usd == 0.0
    assert result.hypothetical_model_cost_usd is not None
    assert result.suggest_match == 2
    assert result.correct_match_suggestions == 1
    assert result.incorrect_match_suggestions == 1
    assert result.suggest_no_match == 2
    assert result.correct_no_match_suggestions == 1
    assert result.incorrect_no_match_suggestions == 1
    assert result.insufficient_evidence == 1
    assert result.provider_failure == 2
    assert result.invalid_outputs == 1
    assert result.authorization_blocked_cases == 1
    assert result.review_case_mutations == 0
    assert result.suggest_match_precision == 0.5
    assert result.suggest_no_match_precision == 0.5
    assert result.provider_failures_safely_routed == 1.0
    text = result.to_text()
    assert "model_quality_claim: false" in text
    assert "cost_mode: SIMULATED" in text
    assert "actual_provider_cost_usd: 0.00000000" in text


def test_scripted_precision_is_not_a_luna_quality_claim() -> None:
    result = run_scripted_offline_benchmark()
    assert result.result_source == "offline_scripted_provider"
    assert result.live is False
    assert SemanticSuggestionType.SUGGEST_MATCH.value not in result.result_source


def test_stratified_sample_requires_both_labelled_classes() -> None:
    cases = [_Case("RC-a"), _Case("RC-b"), _Case("RC-c")]
    selected, reason = select_stratified_labelled_sample(
        cases,
        same_person_by_case_id={"RC-a": True, "RC-b": True, "RC-c": None},
        max_cases=25,
    )
    assert selected == []
    assert reason is not None
    assert "insufficient" in reason
    mixed, reason_ok = select_stratified_labelled_sample(
        cases,
        same_person_by_case_id={"RC-a": True, "RC-b": False, "RC-c": True},
        max_cases=2,
    )
    assert reason_ok is None
    assert [case.review_case_id for case in mixed] == ["RC-a", "RC-b"]
