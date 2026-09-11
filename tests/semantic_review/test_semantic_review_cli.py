from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from human_review.cases import generate_review_cases
from human_review.reporting import write_review_reports
from human_review.workflow import ReviewWorkflow
from scripts import suggest_human_review
from tests.human_review.conftest import make_review_resolution
from tests.semantic_review.conftest import _with_shared_email


def test_cli_fake_suggest_and_inspect(tmp_path: Path, monkeypatch, resolution_config) -> None:
    resolution = _with_shared_email(make_review_resolution("rec-a", "rec-b"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    report_dir = tmp_path / "generated"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    out_dir = tmp_path / "semantic"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "suggest",
            "--report",
            str(report_dir / "human_review_report.json"),
            "--case-id",
            case_id,
            "--fake",
            "--output-dir",
            str(out_dir),
        ],
    )
    assert suggest_human_review.main() == 0
    audits = list(out_dir.glob("LS-*.json"))
    assert audits
    suggestion_id = json.loads(audits[0].read_text(encoding="utf-8"))["suggestion_id"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "inspect",
            "--suggestion-id",
            suggestion_id,
            "--report-dir",
            str(out_dir),
        ],
    )
    assert suggest_human_review.main() == 0
    assert workflow.get_case(case_id).status.value == "PENDING"


def test_cli_live_refused_when_enabled_without_api_key(
    tmp_path: Path, monkeypatch, resolution_config
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = yaml.safe_load(Path("configs/semantic_review.yaml").read_text(encoding="utf-8"))
    source["enabled"] = True
    enabled_path = tmp_path / "semantic_review.yaml"
    enabled_path.write_text(yaml.safe_dump(source), encoding="utf-8")
    resolution = _with_shared_email(make_review_resolution("rec-a", "rec-b"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    report_dir = tmp_path / "generated"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "--config",
            str(enabled_path),
            "suggest",
            "--report",
            str(report_dir / "human_review_report.json"),
            "--case-id",
            case_id,
            "--live",
        ],
    )
    assert suggest_human_review.main() == 5


def test_cli_live_refused_when_disabled(tmp_path: Path, monkeypatch, resolution_config) -> None:
    resolution = _with_shared_email(make_review_resolution("rec-a", "rec-b"))
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    report_dir = tmp_path / "generated"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "suggest",
            "--report",
            str(report_dir / "human_review_report.json"),
            "--case-id",
            case_id,
            "--live",
        ],
    )
    assert suggest_human_review.main() == 5


def test_cli_scripted_benchmark(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "benchmark",
            "--scripted",
            "--output-dir",
            str(tmp_path / "scripted"),
        ],
    )
    assert suggest_human_review.main() == 0


def test_cli_forbidden_split(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suggest_human_review.py",
            "benchmark",
            "--dataset",
            str(tmp_path),
            "--split",
            "final_holdout",
            "--fake",
        ],
    )
    assert suggest_human_review.main() == 4
