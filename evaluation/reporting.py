import json
from pathlib import Path

from evaluation.evaluator.hard_gates import HardGateResult


def build_report_data(
    *,
    dataset_name: str,
    dataset_version: str,
    metrics: dict[str, float],
    gate_results: list[HardGateResult],
    overall_passed: bool,
) -> dict:
    return {
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
        },
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
        "overall_status": "PASS" if overall_passed else "FAIL",
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
        f"**Dataset:** {report_data['dataset']['name']}",
        f"**Version:** {report_data['dataset']['version']}",
        f"**Overall Status:** {report_data['overall_status']}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for metric_name, value in report_data["metrics"].items():
        lines.append(f"| {metric_name} | {value:.4f} |")

    lines.extend(
        [
            "",
            "## Hard Gates",
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
