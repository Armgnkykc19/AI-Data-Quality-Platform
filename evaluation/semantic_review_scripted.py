"""Deterministic OFFLINE_SCRIPTED scenarios. Formula and safety only, not Luna quality."""

from __future__ import annotations

from entity_resolution.config import load_entity_resolution_config
from evaluation.semantic_review_benchmark import (
    BENCHMARK_MODE_OFFLINE_SCRIPTED,
    SemanticReviewBenchmarkResult,
    _ratio,
)
from human_review.cases import generate_review_cases
from human_review.models import ReviewStatus
from human_review.workflow import ReviewWorkflow
from semantic_review.config import SemanticReviewConfig, load_semantic_review_config
from semantic_review.models import CostMode, SemanticFailureCode, SemanticSuggestionType
from semantic_review.scripted_fixtures import build_scripted_scenarios, provider_for_factory
from semantic_review.service import SemanticReviewService


def run_scripted_offline_benchmark(
    *,
    config: SemanticReviewConfig | None = None,
    entity_resolution_config=None,
) -> SemanticReviewBenchmarkResult:
    loaded_config = config or load_semantic_review_config()
    er_config = entity_resolution_config or load_entity_resolution_config()
    result = SemanticReviewBenchmarkResult(
        split_name="scripted",
        result_source="offline_scripted_provider",
        live=False,
        benchmark_mode=BENCHMARK_MODE_OFFLINE_SCRIPTED,
        model_quality_claim=False,
        live_model_observation=False,
        model_quality_status="not_applicable_offline",
        cost_mode=CostMode.SIMULATED.value,
        actual_provider_cost_usd=0.0,
        hypothetical_model_cost_usd=0.0,
        estimated_cost_usd=0.0,
    )
    skipped: dict[str, int] = {}
    failure_events = 0
    safely_routed = 0
    latencies: list[float] = []

    for scenario in build_scripted_scenarios():
        state = generate_review_cases(scenario.resolution, config=er_config)
        workflow = ReviewWorkflow(state)
        case = workflow.state.cases[0]
        records_by_id = {record.record_id: record for record in scenario.resolution.records}
        provider = provider_for_factory(
            scenario.provider_factory, loaded_config.pricing, loaded_config.model
        )
        service = SemanticReviewService(loaded_config, provider, require_enabled=False)
        routing, suggestion = service.suggest_for_case(
            case,
            records_by_id=records_by_id,
            outcome=workflow.to_outcome(),
            resolution=scenario.resolution,
            entity_resolution_config=er_config,
        )
        if scenario.expected_skip_reason is not None:
            reason = routing.reason.value if routing.reason else "ineligible"
            skipped[reason] = skipped.get(reason, 0) + 1
            if routing.reason == scenario.expected_skip_reason:
                result.authorization_blocked_cases += 1
            if suggestion is not None:
                result.authorization_violations += 1
            continue

        result.eligible_review_cases += 1
        if suggestion is None:
            reason = routing.reason.value if routing.reason else "ineligible"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        result.attempted_calls += 1
        result.processed_cases += 1
        latencies.append(float(suggestion.latency_ms))
        result.input_tokens += suggestion.input_token_count
        result.cached_input_tokens += suggestion.cached_input_token_count
        result.output_tokens += suggestion.output_token_count
        result.reasoning_tokens += suggestion.reasoning_token_count
        result.actual_provider_cost_usd = 0.0
        if suggestion.hypothetical_model_cost_usd is not None:
            result.hypothetical_model_cost_usd = round(
                (result.hypothetical_model_cost_usd or 0.0)
                + suggestion.hypothetical_model_cost_usd,
                8,
            )
        result.estimated_cost_usd = result.actual_provider_cost_usd

        if suggestion.suggestion != SemanticSuggestionType.PROVIDER_FAILURE:
            result.successful_calls += 1
        if suggestion.suggestion == SemanticSuggestionType.SUGGEST_MATCH:
            result.suggest_match += 1
        elif suggestion.suggestion == SemanticSuggestionType.SUGGEST_NO_MATCH:
            result.suggest_no_match += 1
        elif suggestion.suggestion == SemanticSuggestionType.INSUFFICIENT_EVIDENCE:
            result.insufficient_evidence += 1
        elif suggestion.suggestion == SemanticSuggestionType.PROVIDER_FAILURE:
            result.provider_failure += 1
            failure_events += 1
            if case.status == ReviewStatus.PENDING:
                safely_routed += 1
            if suggestion.failure_code == SemanticFailureCode.INVALID_STRUCTURED_OUTPUT:
                result.invalid_outputs += 1

        if scenario.same_person is not None:
            result.labeled_processed += 1
            if suggestion.suggestion == SemanticSuggestionType.SUGGEST_MATCH:
                if scenario.same_person:
                    result.correct_match_suggestions += 1
                else:
                    result.incorrect_match_suggestions += 1
            elif suggestion.suggestion == SemanticSuggestionType.SUGGEST_NO_MATCH:
                if scenario.same_person:
                    result.incorrect_no_match_suggestions += 1
                else:
                    result.correct_no_match_suggestions += 1

        if case.status != ReviewStatus.PENDING:
            result.review_case_mutations += 1

    result.skipped_by_reason = skipped
    result.exclusions = dict(skipped)
    result.suggest_match_precision = _ratio(
        result.correct_match_suggestions,
        result.correct_match_suggestions + result.incorrect_match_suggestions,
    )
    result.suggest_no_match_precision = _ratio(
        result.correct_no_match_suggestions,
        result.correct_no_match_suggestions + result.incorrect_no_match_suggestions,
    )
    decided = (
        result.correct_match_suggestions
        + result.incorrect_match_suggestions
        + result.correct_no_match_suggestions
        + result.incorrect_no_match_suggestions
    )
    result.suggestion_accuracy = _ratio(
        result.correct_match_suggestions + result.correct_no_match_suggestions,
        decided,
    )
    result.suggestion_coverage = _ratio(
        result.suggest_match + result.suggest_no_match,
        result.processed_cases,
    )
    result.abstention_rate = _ratio(result.insufficient_evidence, result.processed_cases)
    result.failure_rate = _ratio(result.provider_failure, result.processed_cases)
    result.invalid_output_rate = _ratio(result.invalid_outputs, result.processed_cases)
    result.average_latency_ms = sum(latencies) / len(latencies) if latencies else None
    result.p95_latency_ms = None if not latencies else float(sorted(latencies)[-1])
    result.average_cost_usd = _ratio(result.actual_provider_cost_usd or 0.0, result.processed_cases)
    result.provider_failures_safely_routed = _ratio(safely_routed, failure_events)
    result.direct_merge_attempts = 0
    return result
