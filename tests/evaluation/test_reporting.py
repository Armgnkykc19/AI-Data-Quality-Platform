import json
from pathlib import Path

from evaluation.evaluator.hard_gates import HardGateResult
from evaluation.reporting import (
    build_report_data,
    write_json_report,
    write_markdown_report,
)


def build_sample_report() -> dict:
    gate_results = [
        HardGateResult(
            name="auto_merge_precision",
            actual=0.995,
            threshold=0.99,
            operator="gte",
            passed=True,
        )
    ]

    return build_report_data(
        dataset_name="fixture",
        dataset_version="0.1.0",
        metrics={"auto_merge_precision": 0.995},
        gate_results=gate_results,
        overall_passed=True,
    )


def test_build_report_data_contains_expected_structure() -> None:
    report = build_sample_report()

    assert report["dataset"]["name"] == "fixture"
    assert report["dataset"]["version"] == "0.1.0"
    assert report["metrics"]["auto_merge_precision"] == 0.995
    assert report["hard_gates"][0]["passed"] is True
    assert report["overall_status"] == "PASS"


def test_write_json_report(tmp_path: Path) -> None:
    output_path = tmp_path / "report.json"
    report = build_sample_report()

    write_json_report(report, output_path)

    saved_report = json.loads(output_path.read_text(encoding="utf-8"))

    assert saved_report == report


def test_write_markdown_report(tmp_path: Path) -> None:
    output_path = tmp_path / "report.md"
    report = build_sample_report()

    write_markdown_report(report, output_path)

    content = output_path.read_text(encoding="utf-8")

    assert "# Evaluation Report" in content
    assert "**Dataset:** fixture" in content
    assert "**Overall Status:** PASS" in content
    assert "auto_merge_precision" in content
