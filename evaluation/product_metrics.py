"""Product-quality metric collection and acceptance gates for Sprint 7B."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evaluation.evaluator.hard_gates import HardGateResult, evaluate_hard_gates

PRODUCT_METRIC_SOURCES = {
    "entity_resolution_auto_match_precision": "real_entity_resolution_benchmark",
    "entity_resolution_false_match_rate": "real_entity_resolution_benchmark",
    "entity_resolution_candidate_recall": "real_entity_resolution_benchmark",
    "schema_mapping_accuracy": "real_schema_mapping_benchmark",
    "source_b_mapping_accuracy": "real_source_b_mapping_benchmark",
    "critical_field_mapping_recall": "real_schema_mapping_benchmark",
    "normalization_accuracy": "real_normalization_benchmark",
    "survivorship_field_match_rate": "real_survivorship_benchmark",
    "survivorship_conflict_preservation_rate": "real_survivorship_benchmark",
    "silent_row_loss_rate": "row_accounting_audit",
    "review_unresolved_unsafe_merge_violations": "real_review_benchmark",
    "review_no_match_transitive_merge_violations": "real_review_benchmark",
    "review_unauthorized_severe_conflict_merges": "real_review_benchmark",
    "review_human_match_without_provenance_violations": "real_review_benchmark",
}


@dataclass(frozen=True)
class ProductMetricAvailability:
    name: str
    available: bool
    reason: str | None = None


def _metric_from_entity_resolution(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {
            "entity_resolution_auto_match_precision": None,
            "entity_resolution_false_match_rate": None,
            "entity_resolution_candidate_recall": None,
        }
    return {
        "entity_resolution_auto_match_precision": float(result.auto_match_precision),
        "entity_resolution_false_match_rate": float(result.false_match_rate),
        "entity_resolution_candidate_recall": float(result.candidate_recall),
    }


def _metric_from_schema_mapping(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {
            "schema_mapping_accuracy": None,
            "critical_field_mapping_recall": None,
        }
    return {
        "schema_mapping_accuracy": float(result.mapping_accuracy),
        "critical_field_mapping_recall": float(result.critical_field_recall),
    }


def _metric_from_source_b(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {"source_b_mapping_accuracy": None}
    return {"source_b_mapping_accuracy": float(result.mapping_accuracy)}


def _metric_from_normalization(result) -> dict[str, float | None]:
    if result is None:
        return {"normalization_accuracy": None}
    return {"normalization_accuracy": float(result.normalization_accuracy)}


def _metric_from_survivorship(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {
            "survivorship_field_match_rate": None,
            "survivorship_conflict_preservation_rate": None,
        }
    return {
        "survivorship_field_match_rate": float(result.field_match_rate),
        "survivorship_conflict_preservation_rate": float(result.conflict_preservation_rate),
    }


def _metric_from_row_accounting(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {"silent_row_loss_rate": None}
    return {"silent_row_loss_rate": float(result.silent_row_loss_rate)}


def _metric_from_review(result) -> dict[str, float | None]:
    if result is None or not getattr(result, "ran_successfully", False):
        return {
            "review_unresolved_unsafe_merge_violations": None,
            "review_no_match_transitive_merge_violations": None,
            "review_unauthorized_severe_conflict_merges": None,
            "review_human_match_without_provenance_violations": None,
        }
    return {
        "review_unresolved_unsafe_merge_violations": float(
            result.unresolved_unsafe_merge_violations
        ),
        "review_no_match_transitive_merge_violations": float(
            result.no_match_transitive_merge_violations
        ),
        "review_unauthorized_severe_conflict_merges": float(
            result.unauthorized_severe_conflict_merges
        ),
        "review_human_match_without_provenance_violations": float(
            result.human_match_without_provenance_violations
        ),
    }


def collect_product_metrics(
    *,
    entity_resolution_benchmark=None,
    schema_mapping_benchmark=None,
    source_b_mapping_benchmark=None,
    normalization_benchmark=None,
    survivorship_benchmark=None,
    row_accounting_audit=None,
    review_benchmark=None,
) -> tuple[dict[str, float], list[ProductMetricAvailability]]:
    raw: dict[str, float | None] = {}
    raw.update(_metric_from_entity_resolution(entity_resolution_benchmark))
    raw.update(_metric_from_schema_mapping(schema_mapping_benchmark))
    raw.update(_metric_from_source_b(source_b_mapping_benchmark))
    raw.update(_metric_from_normalization(normalization_benchmark))
    raw.update(_metric_from_survivorship(survivorship_benchmark))
    raw.update(_metric_from_row_accounting(row_accounting_audit))
    raw.update(_metric_from_review(review_benchmark))

    availability: list[ProductMetricAvailability] = []
    metrics: dict[str, float] = {}
    for name, value in raw.items():
        if value is None:
            availability.append(
                ProductMetricAvailability(
                    name=name,
                    available=False,
                    reason=f"Missing real benchmark source for {name}",
                )
            )
        else:
            metrics[name] = value
            availability.append(ProductMetricAvailability(name=name, available=True, reason=None))
    return metrics, availability


def evaluate_product_gates(
    *,
    metrics: Mapping[str, float],
    gate_config: Mapping[str, Mapping[str, object]],
) -> tuple[list[HardGateResult], list[str]]:
    missing: list[str] = []
    gate_metrics: dict[str, float] = {}
    normalized_config: dict[str, dict[str, object]] = {}

    for gate_name, gate_definition in gate_config.items():
        metric_name = str(gate_definition.get("metric", gate_name))
        if metric_name not in metrics:
            missing.append(metric_name)
            continue
        gate_metrics[metric_name] = float(metrics[metric_name])
        normalized_config[metric_name] = {
            "operator": gate_definition["operator"],
            "threshold": gate_definition["threshold"],
        }

    if missing:
        raise KeyError(
            "Product gate evaluation missing real metrics (fail closed): "
            + ", ".join(sorted(set(missing)))
        )

    results = evaluate_hard_gates(metrics=gate_metrics, gate_config=normalized_config)
    renamed = [
        HardGateResult(
            name=gate_name,
            actual=result.actual,
            threshold=result.threshold,
            operator=result.operator,
            passed=result.passed,
        )
        for (gate_name, gate_definition), result in zip(
            (
                (name, definition)
                for name, definition in gate_config.items()
                if str(definition.get("metric", name)) in metrics
            ),
            results,
            strict=True,
        )
    ]
    return renamed, missing


def product_metrics_summary(
    metrics: Mapping[str, float],
    availability: list[ProductMetricAvailability],
) -> dict[str, Any]:
    return {
        "metrics": dict(metrics),
        "availability": [
            {
                "name": item.name,
                "available": item.available,
                "reason": item.reason,
                "source": PRODUCT_METRIC_SOURCES.get(item.name),
            }
            for item in availability
        ],
    }
