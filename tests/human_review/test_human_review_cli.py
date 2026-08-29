from __future__ import annotations

import json
import sys
from pathlib import Path

from entity_resolution.models import MatchDecisionType
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision
from human_review.reporting import write_review_reports
from human_review.workflow import ReviewWorkflow
from scripts import build_canonical_entities, manage_human_review
from tests.human_review.conftest import make_bridge_resolution, make_record, make_review_resolution


def _write_csv(path: Path) -> None:
    path.write_text(
        "first_name,last_name,email,phone,company,city,district,address\n"
        "Ali,Yilmaz,ali1@example.com,+905321111111,Acme,Ankara,Cankaya,Street 1\n"
        "Ali,Yilmaz,ali2@example.com,+905322222222,Acme,Ankara,Cankaya,Street 1\n",
        encoding="utf-8",
    )


def test_cli_rejects_invalid_review_report(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "broken.json"
    report.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["manage_human_review.py", "list", str(report)],
    )
    assert manage_human_review.main() == 3


def test_cli_resolve_returns_policy_exit_on_repeat_resolution(
    tmp_path: Path,
    monkeypatch,
    resolution_config,
) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
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
    first_out = tmp_path / "first"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "resolve",
            str(report_dir / "human_review_report.json"),
            case_id,
            "--decision",
            HumanReviewDecision.DEFER.value,
            "--output-report-dir",
            str(first_out),
        ],
    )
    assert manage_human_review.main() == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "resolve",
            str(first_out / "human_review_report.json"),
            case_id,
            "--decision",
            HumanReviewDecision.MATCH.value,
            "--output-report-dir",
            str(tmp_path / "second"),
        ],
    )
    assert manage_human_review.main() == 4


def test_cli_resolve_applies_authorization_boundary(
    tmp_path: Path,
    monkeypatch,
    resolution_config,
) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz", email="c@example.com"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    resolution = make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    report_dir = tmp_path / "bridge"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "resolve",
            str(report_dir / "human_review_report.json"),
            case_id,
            "--decision",
            HumanReviewDecision.MATCH.value,
            "--output-report-dir",
            str(tmp_path / "blocked"),
        ],
    )
    assert manage_human_review.main() == 4


def test_cli_no_match_is_absolute(
    tmp_path: Path,
    monkeypatch,
    resolution_config,
) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
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
    out_dir = tmp_path / "no-match"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "resolve",
            str(report_dir / "human_review_report.json"),
            case_id,
            "--decision",
            HumanReviewDecision.NO_MATCH.value,
            "--output-report-dir",
            str(out_dir),
        ],
    )
    assert manage_human_review.main() == 0
    payload = json.loads((out_dir / "human_review_report.json").read_text(encoding="utf-8"))
    case = payload["workflow_state"]["cases"][0]
    assert case["status"] == "NO_MATCH"
    assert case["resolution"]["human_decision"] == "NO_MATCH"


def test_cli_match_uses_persisted_authorization_context(
    tmp_path: Path,
    monkeypatch,
    resolution_config,
) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
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
    out_dir = tmp_path / "matched"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "resolve",
            str(report_dir / "human_review_report.json"),
            case_id,
            "--decision",
            HumanReviewDecision.MATCH.value,
            "--output-report-dir",
            str(out_dir),
        ],
    )
    assert manage_human_review.main() == 0
    payload = json.loads((out_dir / "human_review_report.json").read_text(encoding="utf-8"))
    case = payload["workflow_state"]["cases"][0]
    assert case["status"] == "MATCH"
    assert case["resolution"]["human_decision"] == "MATCH"
    assert payload["resolution_snapshot"]["auto_match_pairs"] == []
    assert payload["entity_records"]


def test_canonical_cli_applies_human_review_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    csv_path = tmp_path / "input.csv"
    _write_csv(csv_path)
    generate_dir = tmp_path / "review"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "generate",
            str(csv_path),
            "--report-dir",
            str(generate_dir),
        ],
    )
    assert manage_human_review.main() == 0
    report_path = generate_dir / "human_review_report.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert payload["entity_records"]
    assert "auto_match_pairs" in payload["resolution_snapshot"]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_canonical_entities.py",
            str(csv_path),
            "--human-review-report",
            str(report_path),
            "--report-dir",
            str(tmp_path / "canonical"),
        ],
    )
    assert build_canonical_entities.main() == 0
    assert (tmp_path / "canonical" / "survivorship_report.json").exists()


def test_canonical_cli_malformed_report_fail_closed(tmp_path: Path, monkeypatch) -> None:
    csv_path = tmp_path / "input.csv"
    _write_csv(csv_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_canonical_entities.py",
            str(csv_path),
            "--human-review-report",
            str(broken),
        ],
    )
    assert build_canonical_entities.main() == 3


def test_canonical_cli_mismatched_match_pairs_fail_closed(
    tmp_path: Path,
    monkeypatch,
    resolution_config,
) -> None:
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
    )
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    case_id = workflow.list_cases()[0].review_case_id
    workflow.resolve_case(
        case_id,
        decision=HumanReviewDecision.MATCH,
        reviewer_id="reviewer-1",
        resolution=resolution,
        records_by_id={record.record_id: record for record in records},
        entity_resolution_config=resolution_config,
    )
    report_dir = tmp_path / "foreign"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    csv_path = tmp_path / "other.csv"
    csv_path.write_text(
        "first_name,last_name,email,phone,company,city,district,address\n"
        "Ayse,Kaya,ayse@example.com,+905321111113,Acme,Ankara,Cankaya,Street 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_canonical_entities.py",
            str(csv_path),
            "--human-review-report",
            str(report_dir / "human_review_report.json"),
        ],
    )
    assert build_canonical_entities.main() == 3


def test_cli_generate_persists_auto_match_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    csv_path = tmp_path / "input.csv"
    _write_csv(csv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "generate",
            str(csv_path),
            "--report-dir",
            str(tmp_path / "out"),
        ],
    )
    assert manage_human_review.main() == 0
    payload = json.loads(
        (tmp_path / "out" / "human_review_report.json").read_text(encoding="utf-8")
    )
    assert payload["artifact_type"] == "human_review_outcome"
    for decision in payload["resolution_snapshot"]["auto_match_pairs"]:
        assert len(decision) == 2
    assert MatchDecisionType.AUTO_MATCH.value == "AUTO_MATCH"
