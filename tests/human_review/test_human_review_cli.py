from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from entity_resolution.models import MatchDecisionType
from human_review.cases import generate_review_cases
from human_review.models import HumanReviewDecision
from human_review.reporting import write_review_reports
from human_review.workflow import ReviewWorkflow
from scripts import build_canonical_entities, manage_human_review
from tests.human_review.conftest import make_record, make_review_resolution


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


# --------------------------------------------------------------------------
# One authoritative resolution store
#
# These replace four tests that exercised report-based resolution -- repeat
# resolution, the authorization boundary, NO_MATCH absoluteness, and the
# persisted authorization context. That behaviour is gone, not merely
# discouraged, so the tests that proved it worked have been replaced by tests
# that prove it is refused.
#
# None of those domain invariants lost coverage. They were never properties of
# the CLI: the boundary check lives in tests/human_review/
# test_authorization_boundary.py, terminal transitions in
# test_review_cases.py, NO_MATCH absoluteness in test_downstream_integration.py
# and tests/review_application/test_cross_case_concurrency.py, and the
# persisted authorization context in
# tests/review_application/test_review_queue_service.py.
# --------------------------------------------------------------------------


def _resolve_argv(report: Path, case_id: str, out_dir: Path) -> list[str]:
    """The invocation that used to work, spelled exactly as it was."""
    return [
        "manage_human_review.py",
        "resolve",
        str(report),
        case_id,
        "--decision",
        HumanReviewDecision.MATCH.value,
        "--output-report-dir",
        str(out_dir),
    ]


@pytest.fixture
def generated_report(tmp_path: Path, resolution_config) -> tuple[Path, str]:
    """A real report on disk, plus the id of a case inside it."""
    resolution = make_review_resolution("rec-a", "rec-b")
    workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
    report_dir = tmp_path / "generated"
    write_review_reports(
        workflow.to_outcome(),
        output_directory=report_dir,
        entity_records=resolution.records,
        resolution=resolution,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    return report_dir / "human_review_report.json", workflow.list_cases()[0].review_case_id


def test_cli_resolve_is_refused_and_writes_no_report(
    tmp_path: Path,
    monkeypatch,
    generated_report: tuple[Path, str],
) -> None:
    """The old invocation parses, is refused, and produces nothing.

    Refused rather than removed from argparse: an operator who runs a command
    that used to work should be told why and where to go, not handed "invalid
    choice".
    """
    report, case_id = generated_report
    out_dir = tmp_path / "refused"
    monkeypatch.setattr(sys, "argv", _resolve_argv(report, case_id, out_dir))

    assert manage_human_review.main() == 1
    assert not out_dir.exists()


def test_the_refusal_names_the_authoritative_path(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
    generated_report: tuple[Path, str],
) -> None:
    """A refusal without a replacement is an invitation to work around it."""
    report, case_id = generated_report
    monkeypatch.setattr(sys, "argv", _resolve_argv(report, case_id, tmp_path / "refused"))

    manage_human_review.main()
    out = capsys.readouterr().out

    assert "resolve" in out
    assert "/resolve" in out
    assert "review_api" in out


def test_the_report_is_left_exactly_as_it_was(
    tmp_path: Path,
    monkeypatch,
    generated_report: tuple[Path, str],
) -> None:
    """Refusing must not touch the input either."""
    report, case_id = generated_report
    before = report.read_bytes()

    monkeypatch.setattr(sys, "argv", _resolve_argv(report, case_id, tmp_path / "refused"))
    manage_human_review.main()

    assert report.read_bytes() == before


def test_reports_remain_readable_as_an_export(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
    generated_report: tuple[Path, str],
) -> None:
    """A report is still an export; it is only no longer a decision store.

    ``list`` and ``inspect`` are unchanged, which is the distinction Phase A
    draws: reading a report is fine, deciding inside one is not.
    """
    report, case_id = generated_report

    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", "list", str(report)])
    assert manage_human_review.main() == 0
    assert case_id in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", "inspect", str(report), case_id])
    assert manage_human_review.main() == 0
    assert case_id in capsys.readouterr().out


def test_no_cli_command_resolves_a_review_case(monkeypatch) -> None:
    """Pins the absence, so a second authority cannot return unnoticed.

    ``ReviewWorkflow.resolve_case`` is the domain entry point for a decision.
    The operator CLI must not call it at all: the one authoritative path runs
    through ``ReviewQueueService``, which the review API owns.
    """
    source = Path(manage_human_review.__file__).read_text(encoding="utf-8")

    assert "workflow.resolve_case(" not in source
    assert ".resolve_case(" not in source


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
