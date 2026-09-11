"""Offline/live LLM-assistance benchmark. Never mixed into deterministic product gates."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from evaluation.ground_truth import load_evaluation_ground_truth
from evaluation.survivorship_benchmark import _load_entity_records_from_dataset
from human_review.cases import generate_review_cases
from human_review.models import ReviewCase, ReviewStatus
from human_review.workflow import ReviewWorkflow
from semantic_review.budget import LiveBudget
from semantic_review.config import SemanticReviewConfig, load_semantic_review_config
from semantic_review.errors import SemanticReviewBudgetError
from semantic_review.models import CostMode, SemanticFailureCode, SemanticSuggestionType
from semantic_review.policy import evaluate_routing
from semantic_review.provider import SemanticReviewProvider
from semantic_review.providers.fake_provider import insufficient_evidence_provider
from semantic_review.request_builder import FORBIDDEN_PAYLOAD_KEYS, build_semantic_review_request
from semantic_review.service import SemanticReviewService

FORBIDDEN_SEMANTIC_SPLITS = frozenset({"test", "holdout", "final_holdout", "train"})
BENCHMARK_MODE_OFFLINE_FAKE = "OFFLINE_FAKE_SMOKE"
BENCHMARK_MODE_OFFLINE_SCRIPTED = "OFFLINE_SCRIPTED"
BENCHMARK_MODE_LIVE = "LIVE"


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return float(ordered[index])


def _fmt(value: float | None) -> str:
    if value is None:
        return "undefined"
    return f"{value:.4f}"


def _fmt_cost(value: float | None) -> str:
    if value is None:
        return "undefined"
    return f"{value:.8f}"


def select_stratified_labelled_sample(
    cases: list[ReviewCase],
    *,
    same_person_by_case_id: dict[str, bool | None],
    max_cases: int,
) -> tuple[list[ReviewCase], str | None]:
    matches = [case for case in cases if same_person_by_case_id.get(case.review_case_id) is True]
    non_matches = [
        case for case in cases if same_person_by_case_id.get(case.review_case_id) is False
    ]
    matches.sort(key=lambda case: case.review_case_id)
    non_matches.sort(key=lambda case: case.review_case_id)
    if not matches or not non_matches:
        return [], (
            "validation REVIEW population cannot provide both labelled match and "
            "non-match classes; sample is insufficient"
        )
    selected: list[ReviewCase] = []
    match_index = 0
    non_match_index = 0
    while len(selected) < max_cases and (
        match_index < len(matches) or non_match_index < len(non_matches)
    ):
        if match_index < len(matches):
            selected.append(matches[match_index])
            match_index += 1
        if len(selected) >= max_cases:
            break
        if non_match_index < len(non_matches):
            selected.append(non_matches[non_match_index])
            non_match_index += 1
    return selected, None


@dataclass
class SemanticReviewBenchmarkResult:
    split_name: str
    result_source: str
    live: bool
    benchmark_mode: str
    model_quality_claim: bool = False
    live_model_observation: bool = False
    cost_mode: str = CostMode.SIMULATED.value
    eligible_review_cases: int = 0
    attempted_calls: int = 0
    successful_calls: int = 0
    processed_cases: int = 0
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, int] = field(default_factory=dict)
    authorization_blocked_cases: int = 0
    suggest_match: int = 0
    suggest_no_match: int = 0
    insufficient_evidence: int = 0
    provider_failure: int = 0
    invalid_outputs: int = 0
    correct_match_suggestions: int = 0
    incorrect_match_suggestions: int = 0
    correct_no_match_suggestions: int = 0
    incorrect_no_match_suggestions: int = 0
    labeled_processed: int = 0
    suggest_match_precision: float | None = None
    suggest_no_match_precision: float | None = None
    suggestion_accuracy: float | None = None
    suggestion_coverage: float | None = None
    abstention_rate: float | None = None
    failure_rate: float | None = None
    invalid_output_rate: float | None = None
    average_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost_usd: float | None = 0.0
    actual_provider_cost_usd: float | None = 0.0
    hypothetical_model_cost_usd: float | None = 0.0
    average_cost_usd: float | None = None
    authorization_violations: int = 0
    direct_merge_attempts: int = 0
    ground_truth_leakage_violations: int = 0
    review_case_mutations: int = 0
    provider_failures_safely_routed: float | None = None
    sample_insufficient: bool = False
    sample_insufficient_reason: str | None = None
    stopped_reason: str | None = None
    model_quality_status: str = "unverified"
    ran_successfully: bool = True
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_name": self.split_name,
            "result_source": self.result_source,
            "live": self.live,
            "benchmark_mode": self.benchmark_mode,
            "model_quality_claim": self.model_quality_claim,
            "live_model_observation": self.live_model_observation,
            "model_quality_status": self.model_quality_status,
            "cost_mode": self.cost_mode,
            "eligible_review_cases": self.eligible_review_cases,
            "attempted_calls": self.attempted_calls,
            "successful_calls": self.successful_calls,
            "processed_cases": self.processed_cases,
            "skipped_by_reason": dict(self.skipped_by_reason),
            "exclusions": dict(self.exclusions),
            "authorization_blocked_cases": self.authorization_blocked_cases,
            "suggest_match": self.suggest_match,
            "suggest_no_match": self.suggest_no_match,
            "insufficient_evidence": self.insufficient_evidence,
            "provider_failure": self.provider_failure,
            "invalid_outputs": self.invalid_outputs,
            "correct_match_suggestions": self.correct_match_suggestions,
            "incorrect_match_suggestions": self.incorrect_match_suggestions,
            "correct_no_match_suggestions": self.correct_no_match_suggestions,
            "incorrect_no_match_suggestions": self.incorrect_no_match_suggestions,
            "labeled_processed": self.labeled_processed,
            "suggest_match_precision": self.suggest_match_precision,
            "suggest_no_match_precision": self.suggest_no_match_precision,
            "suggestion_accuracy": self.suggestion_accuracy,
            "suggestion_coverage": self.suggestion_coverage,
            "abstention_rate": self.abstention_rate,
            "failure_rate": self.failure_rate,
            "invalid_output_rate": self.invalid_output_rate,
            "average_latency_ms": self.average_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "actual_provider_cost_usd": self.actual_provider_cost_usd,
            "hypothetical_model_cost_usd": self.hypothetical_model_cost_usd,
            "average_cost_usd": self.average_cost_usd,
            "authorization_violations": self.authorization_violations,
            "direct_merge_attempts": self.direct_merge_attempts,
            "ground_truth_leakage_violations": self.ground_truth_leakage_violations,
            "review_case_mutations": self.review_case_mutations,
            "provider_failures_safely_routed": self.provider_failures_safely_routed,
            "sample_insufficient": self.sample_insufficient,
            "sample_insufficient_reason": self.sample_insufficient_reason,
            "stopped_reason": self.stopped_reason,
            "ran_successfully": self.ran_successfully,
            "error_message": self.error_message,
        }

    def to_text(self) -> str:
        lines = [
            "Semantic Review Benchmark (advisory only; not a product hard gate)",
            "------------------",
            f"benchmark_mode: {self.benchmark_mode}",
            f"model_quality_claim: {str(self.model_quality_claim).lower()}",
            f"live_model_observation: {str(self.live_model_observation).lower()}",
            f"model_quality_status: {self.model_quality_status}",
            f"split: {self.split_name}",
            f"result_source: {self.result_source}",
            f"live: {self.live}",
            f"cost_mode: {self.cost_mode}",
            f"eligible_cases: {self.eligible_review_cases}",
            f"attempted_calls: {self.attempted_calls}",
            f"successful_calls: {self.successful_calls}",
            f"provider_failures: {self.provider_failure}",
            f"invalid_outputs: {self.invalid_outputs}",
            f"processed_cases: {self.processed_cases}",
            f"SUGGEST_MATCH: {self.suggest_match}",
            f"SUGGEST_NO_MATCH: {self.suggest_no_match}",
            f"INSUFFICIENT_EVIDENCE: {self.insufficient_evidence}",
            f"match_suggestion_precision: {_fmt(self.suggest_match_precision)}",
            f"no_match_suggestion_precision: {_fmt(self.suggest_no_match_precision)}",
            f"suggestion_accuracy: {_fmt(self.suggestion_accuracy)}",
            f"abstention_rate: {_fmt(self.abstention_rate)}",
            f"actual_provider_cost_usd: {_fmt_cost(self.actual_provider_cost_usd)}",
            f"average_cost_usd: {_fmt_cost(self.average_cost_usd)}",
            f"mean_latency_ms: {_fmt(self.average_latency_ms)}",
            f"p95_latency_ms: {_fmt(self.p95_latency_ms)}",
            "",
            "Deterministic baseline, LLM suggestions, and human decisions remain separate.",
            "LLM suggestions are not counted as canonical merges.",
            "Fake or scripted accuracy is not GPT-5.6 Luna quality.",
        ]
        return "\n".join(lines)

    def to_markdown(self) -> str:
        return (
            "# Semantic Review Benchmark\n\n"
            "Advisory only. Not a deterministic product hard gate.\n\n"
            f"model_quality_claim: `{str(self.model_quality_claim).lower()}`\n"
            f"live_model_observation: `{str(self.live_model_observation).lower()}`\n"
        )


def write_semantic_benchmark_reports(
    result: SemanticReviewBenchmarkResult, output_directory: Path
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "benchmark.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_directory / "benchmark.md").write_text(result.to_markdown(), encoding="utf-8")
    (output_directory / "benchmark.txt").write_text(result.to_text() + "\n", encoding="utf-8")


def _add_cost(current: float | None, delta: float | None) -> float | None:
    if current is None and delta is None:
        return None
    return round((current or 0.0) + (delta or 0.0), 8)


def run_semantic_review_benchmark(
    *,
    dataset_path: Path,
    split_name: str = "validation",
    config: SemanticReviewConfig | None = None,
    provider: SemanticReviewProvider | None = None,
    require_enabled: bool = False,
    max_cases: int | None = 25,
    stratify_labelled: bool | None = None,
    benchmark_mode: str | None = None,
) -> SemanticReviewBenchmarkResult:
    if split_name in FORBIDDEN_SEMANTIC_SPLITS:
        raise ValueError(
            f"Sprint 09 semantic benchmark/tuning forbids split '{split_name}'. Use validation."
        )
    loaded_config = config or load_semantic_review_config()
    selected_provider = provider or insufficient_evidence_provider(
        loaded_config.pricing, loaded_config.model
    )
    live = selected_provider.live
    if benchmark_mode is None:
        benchmark_mode = BENCHMARK_MODE_LIVE if live else BENCHMARK_MODE_OFFLINE_FAKE
    if stratify_labelled is None:
        stratify_labelled = live
    result = SemanticReviewBenchmarkResult(
        split_name=split_name,
        result_source=("live_openai" if live else "offline_fake_provider"),
        live=live,
        benchmark_mode=benchmark_mode,
        model_quality_claim=False,
        live_model_observation=live,
        cost_mode=CostMode.LIVE.value if live else CostMode.SIMULATED.value,
        actual_provider_cost_usd=0.0,
        hypothetical_model_cost_usd=0.0,
        estimated_cost_usd=0.0,
    )
    try:
        ground_truth = load_evaluation_ground_truth(dataset_path)
        split_person_ids = set(ground_truth.splits.get(split_name, []))
        if not split_person_ids:
            raise ValueError(f"Split '{split_name}' not found or empty.")

        all_records = _load_entity_records_from_dataset(dataset_path)
        records = [
            record
            for record in all_records
            if ground_truth.person_mappings.get(record.record_id) in split_person_ids
        ]
        resolution_config = load_entity_resolution_config()
        resolution = resolve_entities(
            records, source_label=f"semantic-review-benchmark-{split_name}"
        )
        workflow_state = generate_review_cases(resolution, config=resolution_config)
        workflow = ReviewWorkflow(workflow_state)
        records_by_id = {record.record_id: record for record in records}
        budget = LiveBudget(loaded_config) if live else None
        service = SemanticReviewService(
            loaded_config,
            selected_provider,
            require_enabled=require_enabled,
            budget=budget,
        )

        latencies: list[float] = []
        failure_events = 0
        safely_routed_failures = 0
        skipped: Counter[str] = Counter()
        statuses_before = {case.review_case_id: case.status for case in workflow_state.cases}
        eligible_cases: list[ReviewCase] = []
        same_person_by_case_id: dict[str, bool | None] = {}

        for case in sorted(workflow_state.cases, key=lambda item: item.review_case_id):
            routing = evaluate_routing(
                case,
                config=loaded_config,
                outcome=workflow.to_outcome(),
                resolution=resolution,
                records_by_id=records_by_id,
                entity_resolution_config=resolution_config,
                require_enabled=require_enabled,
            )
            person_a = ground_truth.person_mappings.get(case.pair.record_a_id)
            person_b = ground_truth.person_mappings.get(case.pair.record_b_id)
            if person_a is not None and person_b is not None:
                same_person_by_case_id[case.review_case_id] = person_a == person_b
            else:
                same_person_by_case_id[case.review_case_id] = None
            if not routing.eligible:
                reason = routing.reason.value if routing.reason else "ineligible"
                skipped[reason] += 1
                continue
            eligible_cases.append(case)

        result.eligible_review_cases = len(eligible_cases)
        result.skipped_by_reason = dict(skipped)
        result.exclusions = dict(skipped)
        result.authorization_blocked_cases = skipped.get("AUTHORIZATION_BLOCKED", 0)

        selected_cases = eligible_cases
        if stratify_labelled:
            selected_cases, insufficient_reason = select_stratified_labelled_sample(
                eligible_cases,
                same_person_by_case_id=same_person_by_case_id,
                max_cases=max_cases if max_cases is not None else 25,
            )
            if insufficient_reason:
                result.sample_insufficient = True
                result.sample_insufficient_reason = insufficient_reason
                result.model_quality_status = "unverified"
                result.model_quality_claim = False
                return result
        elif max_cases is not None:
            selected_cases = eligible_cases[:max_cases]

        for case in selected_cases:
            if max_cases is not None and result.attempted_calls >= max_cases:
                result.stopped_reason = "call_limit"
                break
            try:
                routing, suggestion = service.suggest_for_case(
                    case,
                    records_by_id=records_by_id,
                    outcome=workflow.to_outcome(),
                    resolution=resolution,
                    entity_resolution_config=resolution_config,
                )
            except SemanticReviewBudgetError as exc:
                result.stopped_reason = "budget_limit"
                result.error_message = str(exc)
                break
            if suggestion is None:
                reason = routing.reason.value if routing.reason else "ineligible"
                skipped[reason] += 1
                continue
            result.attempted_calls += 1
            result.processed_cases += 1
            latencies.append(float(suggestion.latency_ms))
            result.input_tokens += suggestion.input_token_count
            result.cached_input_tokens += suggestion.cached_input_token_count
            result.output_tokens += suggestion.output_token_count
            result.reasoning_tokens += suggestion.reasoning_token_count
            result.actual_provider_cost_usd = _add_cost(
                result.actual_provider_cost_usd, suggestion.actual_provider_cost_usd
            )
            result.hypothetical_model_cost_usd = _add_cost(
                result.hypothetical_model_cost_usd,
                suggestion.hypothetical_model_cost_usd,
            )
            result.estimated_cost_usd = result.actual_provider_cost_usd
            result.cost_mode = suggestion.cost_mode.value
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
                    safely_routed_failures += 1
                if suggestion.failure_code == SemanticFailureCode.INVALID_STRUCTURED_OUTPUT:
                    result.invalid_outputs += 1

            try:
                request = build_semantic_review_request(
                    case,
                    records_by_id,
                    config=loaded_config,
                )
                encoded = str(request.to_dict())
                for forbidden in FORBIDDEN_PAYLOAD_KEYS:
                    if forbidden in encoded:
                        result.ground_truth_leakage_violations += 1
            except ValueError:
                result.ground_truth_leakage_violations += 1

            same_person = same_person_by_case_id.get(case.review_case_id)
            if same_person is not None:
                result.labeled_processed += 1
                if suggestion.suggestion == SemanticSuggestionType.SUGGEST_MATCH:
                    if same_person:
                        result.correct_match_suggestions += 1
                    else:
                        result.incorrect_match_suggestions += 1
                elif suggestion.suggestion == SemanticSuggestionType.SUGGEST_NO_MATCH:
                    if same_person:
                        result.incorrect_no_match_suggestions += 1
                    else:
                        result.correct_no_match_suggestions += 1

        result.skipped_by_reason = dict(skipped)
        result.exclusions = dict(skipped)
        result.authorization_blocked_cases = skipped.get("AUTHORIZATION_BLOCKED", 0)
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
        result.average_latency_ms = mean(latencies) if latencies else None
        result.p95_latency_ms = _percentile(latencies, 95)
        result.average_cost_usd = _ratio(
            result.actual_provider_cost_usd or 0.0,
            result.processed_cases,
        )
        result.provider_failures_safely_routed = _ratio(safely_routed_failures, failure_events)
        result.direct_merge_attempts = 0
        result.authorization_violations = 0
        for case in workflow.state.cases:
            if case.status != statuses_before[case.review_case_id]:
                result.review_case_mutations += 1
        if live:
            result.model_quality_claim = False
            result.live_model_observation = True
            result.model_quality_status = "live_observation_only"
        else:
            result.model_quality_claim = False
            result.live_model_observation = False
            result.model_quality_status = "not_applicable_offline"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)
    return result
