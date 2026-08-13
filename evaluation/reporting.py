import json
from pathlib import Path

from evaluation.evaluator.hard_gates import HardGateResult

EVALUATION_MODE_FIXTURE_SMOKE = "FIXTURE_SMOKE"
PRODUCT_QUALITY_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"


def build_report_data(
    *,
    dataset_name: str,
    dataset_version: str,
    metrics: dict[str, float],
    gate_results: list[HardGateResult],
    overall_passed: bool,
    evaluation_mode: str = EVALUATION_MODE_FIXTURE_SMOKE,
    product_quality_evaluation: str = PRODUCT_QUALITY_NOT_YET_AVAILABLE,
) -> dict:
    hard_gate_status = "PASS" if overall_passed else "FAIL"

    return {
        "evaluation_mode": evaluation_mode,
        "product_quality_evaluation": product_quality_evaluation,
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
