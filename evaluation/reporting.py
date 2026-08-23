import json
from pathlib import Path
from typing import Any

from evaluation.evaluator.hard_gates import HardGateResult

EVALUATION_MODE_FIXTURE_SMOKE = "FIXTURE_SMOKE"
EVALUATION_MODE_MIXED = "MIXED"
PRODUCT_QUALITY_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
PRODUCT_QUALITY_PARTIALLY_AVAILABLE = "PARTIALLY_AVAILABLE"
ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
ENTITY_RESOLUTION_QUALITY_AVAILABLE = "AVAILABLE"
SURVIVORSHIP_QUALITY_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
SURVIVORSHIP_QUALITY_AVAILABLE = "AVAILABLE"
SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
SCHEMA_MAPPING_QUALITY_AVAILABLE = "AVAILABLE"


def build_report_data(
    *,
    dataset_name: str,
    dataset_version: str,
    metrics: dict[str, float],
    gate_results: list[HardGateResult],
    overall_passed: bool,
    evaluation_mode: str = EVALUATION_MODE_FIXTURE_SMOKE,
    product_quality_evaluation: str = PRODUCT_QUALITY_NOT_YET_AVAILABLE,
    real_validation_benchmark: dict[str, Any] | None = None,
    real_normalization_benchmark: dict[str, Any] | None = None,
    real_schema_mapping_benchmark: dict[str, Any] | None = None,
    real_source_b_mapping_benchmark: dict[str, Any] | None = None,
    real_entity_resolution_benchmark: dict[str, Any] | None = None,
    real_survivorship_benchmark: dict[str, Any] | None = None,
    schema_mapping_quality: str = SCHEMA_MAPPING_QUALITY_NOT_YET_AVAILABLE,
    entity_resolution_quality: str = ENTITY_RESOLUTION_QUALITY_NOT_YET_AVAILABLE,
    survivorship_quality: str = SURVIVORSHIP_QUALITY_NOT_YET_AVAILABLE,
) -> dict:
    hard_gate_status = "PASS" if overall_passed else "FAIL"

    report: dict[str, Any] = {
        "evaluation_mode": evaluation_mode,
        "product_quality_evaluation": product_quality_evaluation,
        "entity_resolution_quality": entity_resolution_quality,
        "survivorship_quality": survivorship_quality,
        "schema_mapping_quality": schema_mapping_quality,
        "hard_gate_status": hard_gate_status,
        "overall_infrastructure_status": hard_gate_status,
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
        },
        "metrics_source": "fixture_smoke",
        "metrics": metrics,
        "hard_gates": [
            {
                "name": result.name,
                "actual": result.actual,
                "threshold": result.threshold,
                "operator": result.operator,
                "passed": result.passed,
            }
            for result in gate_results
        ],
        "overall_status": hard_gate_status,
    }

    if real_validation_benchmark is not None:
        report["real_validation_benchmark"] = real_validation_benchmark
    if real_normalization_benchmark is not None:
        report["real_normalization_benchmark"] = real_normalization_benchmark
    if real_schema_mapping_benchmark is not None:
        report["real_schema_mapping_benchmark"] = real_schema_mapping_benchmark
    if real_source_b_mapping_benchmark is not None:
        report["real_source_b_mapping_benchmark"] = real_source_b_mapping_benchmark
    if real_entity_resolution_benchmark is not None:
        report["real_entity_resolution_benchmark"] = real_entity_resolution_benchmark
    if real_survivorship_benchmark is not None:
        report["real_survivorship_benchmark"] = real_survivorship_benchmark

    return report


def write_json_report(report_data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_markdown_report(report_data: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Evaluation Report",
        "",
        f"**Evaluation Mode:** {report_data['evaluation_mode']}",
        f"**Product Quality Evaluation:** {report_data['product_quality_evaluation']}",
        f"**Entity Resolution Quality:** {report_data['entity_resolution_quality']}",
        f"**Survivorship Quality:** {report_data['survivorship_quality']}",
        f"**Schema Mapping Quality:** {report_data['schema_mapping_quality']}",
        f"**Dataset:** {report_data['dataset']['name']}",
        f"**Version:** {report_data['dataset']['version']}",
        f"**Hard Gate Status:** {report_data['hard_gate_status']}",
        f"**Overall Infrastructure Status:** {report_data['overall_infrastructure_status']}",
        "",
        "## Fixture Smoke Metrics (Infrastructure Only)",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for metric_name, value in report_data["metrics"].items():
        lines.append(f"| {metric_name} | {value:.4f} |")

    real_validation = report_data.get("real_validation_benchmark")
    if isinstance(real_validation, dict):
        lines.extend(
            [
                "",
                "## Real Validation Benchmark",
                "",
                f"**Positive Class:** {real_validation.get('positive_class_definition', '')}",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| precision | {real_validation.get('precision', 0.0):.4f} |",
                f"| recall | {real_validation.get('recall', 0.0):.4f} |",
                f"| f1 | {real_validation.get('f1', 0.0):.4f} |",
                f"| true_positives | {real_validation.get('true_positives', 0)} |",
                f"| false_positives | {real_validation.get('false_positives', 0)} |",
                f"| false_negatives | {real_validation.get('false_negatives', 0)} |",
            ]
        )

    real_normalization = report_data.get("real_normalization_benchmark")
    if isinstance(real_normalization, dict):
        lines.extend(
            [
                "",
                "## Real Deterministic Normalization Benchmark",
                "",
                "| Metric | Value |",
                "|---|---:|",
                (
                    f"| normalization_accuracy | "
                    f"{real_normalization.get('normalization_accuracy', 0.0):.4f} |"
                ),
                (
                    f"| expected_transformations | "
                    f"{real_normalization.get('expected_transformations', 0)} |"
                ),
                (
                    f"| correct_transformations | "
                    f"{real_normalization.get('correct_transformations', 0)} |"
                ),
                (
                    f"| missed_transformations | "
                    f"{real_normalization.get('missed_transformations', 0)} |"
                ),
                (
                    f"| incorrect_transformations | "
                    f"{real_normalization.get('incorrect_transformations', 0)} |"
                ),
            ]
        )

    real_schema_mapping = report_data.get("real_schema_mapping_benchmark")
    if isinstance(real_schema_mapping, dict):
        lines.extend(
            [
                "",
                "## Real Schema Mapping Benchmark",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| mapping_accuracy | {real_schema_mapping.get('mapping_accuracy', 0.0):.4f} |",
                f"| precision | {real_schema_mapping.get('precision', 0.0):.4f} |",
                f"| recall | {real_schema_mapping.get('recall', 0.0):.4f} |",
                f"| f1 | {real_schema_mapping.get('f1', 0.0):.4f} |",
                (
                    f"| auto_map_precision | "
                    f"{real_schema_mapping.get('auto_map_precision', 0.0):.4f} |"
                ),
                (
                    f"| review_routing_recall | "
                    f"{real_schema_mapping.get('review_routing_recall', 0.0):.4f} |"
                ),
            ]
        )

    real_entity_resolution = report_data.get("real_entity_resolution_benchmark")
    if isinstance(real_entity_resolution, dict):
        lines.extend(
            [
                "",
                "## Real Entity Resolution Benchmark",
                "",
                "| Metric | Value |",
                "|---|---:|",
                (
                    f"| candidate_recall | "
                    f"{real_entity_resolution.get('candidate_recall', 0.0):.4f} |"
                ),
                f"| precision | {real_entity_resolution.get('precision', 0.0):.4f} |",
                f"| recall | {real_entity_resolution.get('recall', 0.0):.4f} |",
                f"| f1 | {real_entity_resolution.get('f1', 0.0):.4f} |",
                (
                    f"| auto_match_precision | "
                    f"{real_entity_resolution.get('auto_match_precision', 0.0):.4f} |"
                ),
                (
                    f"| false_match_rate | "
                    f"{real_entity_resolution.get('false_match_rate', 0.0):.4f} |"
                ),
                (
                    f"| candidate_reduction_ratio | "
                    f"{real_entity_resolution.get('candidate_reduction_ratio', 0.0):.4f} |"
                ),
            ]
        )

    real_survivorship = report_data.get("real_survivorship_benchmark")
    if isinstance(real_survivorship, dict):
        lines.extend(
            [
                "",
                "## Real Survivorship Benchmark",
                "",
                "| Metric | Value |",
                "|---|---:|",
                (
                    f"| merge_coherence_rate | "
                    f"{real_survivorship.get('merge_coherence_rate', 0.0):.4f} |"
                ),
                (
                    f"| field_match_rate | "
                    f"{real_survivorship.get('field_match_rate', 0.0):.4f} |"
                ),
                (
                    f"| conflict_preservation_rate | "
                    f"{real_survivorship.get('conflict_preservation_rate', 0.0):.4f} |"
                ),
                (
                    f"| preserved_conflict_count | "
                    f"{real_survivorship.get('preserved_conflict_count', 0)} |"
                ),
            ]
        )

    lines.extend(
        [
            "",
            "## Hard Gates (Fixture Smoke)",
            "",
            "| Gate | Actual | Operator | Threshold | Status |",
            "|---|---:|:---:|---:|:---:|",
        ]
    )

    for gate in report_data["hard_gates"]:
        status = "PASS" if gate["passed"] else "FAIL"
        lines.append(
            f"| {gate['name']} | {gate['actual']:.4f} | "
            f"{gate['operator']} | {gate['threshold']:.4f} | {status} |"
        )

    output_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
