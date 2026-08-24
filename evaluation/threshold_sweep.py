"""Validation-split-only entity resolution threshold sweep (recommendation only)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import MatchDecisionType
from entity_resolution.records import build_entity_records_from_quality_result
from evaluation.ground_truth import (
    filter_pairs_for_split,
    load_evaluation_ground_truth,
    pair_key,
)
from ingestion.parser import parse_file
from record_quality.pipeline import run_quality_pipeline

FORBIDDEN_CALIBRATION_SPLITS = frozenset({"test", "final_holdout"})


@dataclass(frozen=True)
class ThresholdSweepPoint:
    threshold: float
    auto_match_total: int
    auto_match_precision: float
    false_match_rate: float
    auto_match_coverage: float


@dataclass
class ThresholdSweepResult:
    split_name: str
    current_threshold: float
    candidate_thresholds: tuple[float, ...] = ()
    points: tuple[ThresholdSweepPoint, ...] = ()
    recommended_threshold: float | None = None
    recommendation_reason: str = ""
    messages: list[str] = field(default_factory=list)
    ran_successfully: bool = True
    error_message: str | None = None


def _assert_calibration_split_allowed(split_name: str) -> None:
    if split_name in FORBIDDEN_CALIBRATION_SPLITS:
        raise ValueError(
            f"Threshold sweep is forbidden on split '{split_name}'. "
            f"Use validation only; never test or final_holdout."
        )


def _load_entity_records(dataset_path: Path) -> list:
    records = []
    for pattern in ("sources/*.csv", "hard_cases/*.csv"):
        for source_path in sorted(dataset_path.glob(pattern)):
            parsed = parse_file(source_path)
            quality = run_quality_pipeline(parsed)
            records.extend(build_entity_records_from_quality_result(parsed, quality))
    return records


def run_threshold_sweep(
    *,
    dataset_path: Path,
    split_name: str = "validation",
    candidate_thresholds: tuple[float, ...] | None = None,
) -> ThresholdSweepResult:
    _assert_calibration_split_allowed(split_name)
    config = load_entity_resolution_config()
    result = ThresholdSweepResult(
        split_name=split_name,
        current_threshold=config.auto_match_threshold,
    )
    thresholds = candidate_thresholds or (
        0.82,
        0.84,
        0.86,
        0.88,
        0.90,
        0.92,
    )
    result.candidate_thresholds = thresholds

    try:
        ground_truth = load_evaluation_ground_truth(dataset_path)
        split_person_ids = set(ground_truth.splits.get(split_name, []))
        if not split_person_ids:
            raise ValueError(f"Split '{split_name}' not found or empty.")

        records = [
            record
            for record in _load_entity_records(dataset_path)
            if ground_truth.person_mappings.get(record.record_id) in split_person_ids
        ]
        positive_pairs = filter_pairs_for_split(
            ground_truth.positive_pairs,
            person_mappings=ground_truth.person_mappings,
            split_person_ids=split_person_ids,
        )

        points: list[ThresholdSweepPoint] = []
        for threshold in thresholds:
            sweep_config = replace(config, auto_match_threshold=threshold)
            resolution = resolve_entities(
                records,
                source_label=f"sweep-{split_name}",
                config=sweep_config,
            )
            decision_map = {
                pair_key(decision.pair.record_a_id, decision.pair.record_b_id): decision
                for decision in resolution.decisions
            }
            auto_match_total = 0
            auto_match_correct = 0
            auto_match_incorrect = 0
            auto_match_on_positives = 0

            for pair in positive_pairs:
                key = pair_key(pair.source_record_id_a, pair.source_record_id_b)
                decision = decision_map.get(key)
                predicted = (
                    decision is not None and decision.decision == MatchDecisionType.AUTO_MATCH
                )
                if predicted:
                    auto_match_on_positives += 1
                if decision is not None and decision.decision == MatchDecisionType.AUTO_MATCH:
                    auto_match_total += 1
                    if pair.person_id_a == pair.person_id_b:
                        auto_match_correct += 1
                    else:
                        auto_match_incorrect += 1

            auto_match_precision = (
                auto_match_correct / auto_match_total if auto_match_total else 1.0
            )
            false_match_rate = auto_match_incorrect / auto_match_total if auto_match_total else 0.0
            coverage = auto_match_on_positives / len(positive_pairs) if positive_pairs else 1.0
            points.append(
                ThresholdSweepPoint(
                    threshold=threshold,
                    auto_match_total=auto_match_total,
                    auto_match_precision=auto_match_precision,
                    false_match_rate=false_match_rate,
                    auto_match_coverage=coverage,
                )
            )

        result.points = tuple(points)
        result.recommended_threshold, result.recommendation_reason = _recommend_threshold(
            current_threshold=config.auto_match_threshold,
            points=result.points,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)

    return result


def _recommend_threshold(
    *,
    current_threshold: float,
    points: tuple[ThresholdSweepPoint, ...],
) -> tuple[float, str]:
    safe_points = [
        point
        for point in points
        if point.false_match_rate == 0.0 and point.auto_match_precision >= 0.99
    ]
    if not safe_points:
        return (
            current_threshold,
            "Keep current threshold; no candidate achieved zero false AUTO_MATCH "
            "with precision >= 0.99 on validation.",
        )

    best = max(safe_points, key=lambda item: (item.auto_match_coverage, -item.threshold))
    if abs(best.threshold - current_threshold) < 1e-9:
        return (
            current_threshold,
            "Keep current threshold; validation evidence supports the existing value.",
        )
    if best.auto_match_coverage <= next(
        point.auto_match_coverage
        for point in points
        if abs(point.threshold - current_threshold) < 1e-9
    ):
        return (
            current_threshold,
            "Keep current threshold; recommended candidate does not improve safe coverage.",
        )
    return (
        best.threshold,
        "Recommendation only — do not auto-write to production config. "
        f"Validation suggests threshold {best.threshold:.2f} for higher safe coverage.",
    )


def threshold_sweep_to_dict(result: ThresholdSweepResult) -> dict[str, Any]:
    return {
        "split_name": result.split_name,
        "current_threshold": result.current_threshold,
        "candidate_thresholds": list(result.candidate_thresholds),
        "recommended_threshold": result.recommended_threshold,
        "recommendation_reason": result.recommendation_reason,
        "points": [
            {
                "threshold": point.threshold,
                "auto_match_total": point.auto_match_total,
                "auto_match_precision": point.auto_match_precision,
                "false_match_rate": point.false_match_rate,
                "auto_match_coverage": point.auto_match_coverage,
            }
            for point in result.points
        ],
        "ran_successfully": result.ran_successfully,
        "error_message": result.error_message,
    }
