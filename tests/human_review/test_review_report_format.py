from __future__ import annotations

from pathlib import Path

import pytest

from human_review.cases import generate_review_cases
from human_review.errors import HumanReviewReportError
from human_review.reporting import (
    REVIEW_REPORT_ARTIFACT_TYPE,
    REVIEW_REPORT_SCHEMA_VERSION,
    load_human_review_report,
    write_review_reports,
)
from human_review.workflow import ReviewWorkflow
from tests.human_review.conftest import make_review_resolution


def test_write_and_load_review_report_roundtrip(tmp_path: Path, resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    report_path = write_review_reports(
        workflow.to_outcome(),
        output_directory=tmp_path,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    loaded = load_human_review_report(report_path)
    assert loaded.payload["schema_version"] == REVIEW_REPORT_SCHEMA_VERSION
    assert loaded.payload["artifact_type"] == REVIEW_REPORT_ARTIFACT_TYPE
    assert len(loaded.entity_records) == 2
    assert loaded.outcome.cases[0].review_case_id == workflow.list_cases()[0].review_case_id
    assert loaded.resolution.records[0].record_id in {"rec-a", "rec-b"}


def test_load_review_report_rejects_missing_schema(tmp_path: Path) -> None:
    path = tmp_path / "human_review_report.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(HumanReviewReportError, match="schema_version"):
        load_human_review_report(path)


def test_load_review_report_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "human_review_report.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HumanReviewReportError, match="not valid JSON"):
        load_human_review_report(path)


def test_load_review_report_rejects_missing_snapshot(tmp_path: Path, resolution_config) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    report_path = write_review_reports(
        workflow.to_outcome(),
        output_directory=tmp_path,
        entity_records=resolution.records,
        resolution=resolution,
    )
    payload = report_path.read_text(encoding="utf-8")
    report_path.write_text(
        payload.replace('"resolution_snapshot"', '"other_snapshot"'),
        encoding="utf-8",
    )
    with pytest.raises(HumanReviewReportError, match="resolution_snapshot"):
        load_human_review_report(report_path)


def test_write_review_report_requires_resolution_snapshot(
    tmp_path: Path,
    resolution_config,
) -> None:
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    with pytest.raises(HumanReviewReportError, match="entity records"):
        write_review_reports(workflow.to_outcome(), output_directory=tmp_path)
