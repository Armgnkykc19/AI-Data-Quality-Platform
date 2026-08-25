#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from entity_resolution.config import load_entity_resolution_config  # noqa: E402
from entity_resolution.engine import resolve_entities  # noqa: E402
from entity_resolution.records import build_entity_records_from_quality_result  # noqa: E402
from human_review.cases import generate_review_cases  # noqa: E402
from human_review.models import HumanReviewDecision  # noqa: E402
from human_review.reporting import write_review_reports  # noqa: E402
from human_review.workflow import ReviewWorkflow  # noqa: E402
from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and resolve deterministic human review cases for entity resolution."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate", help="Generate review cases from input data."
    )
    generate_parser.add_argument("input_path", type=Path)
    generate_parser.add_argument("--report-dir", type=Path, required=True)

    list_parser = subparsers.add_parser("list", help="List review cases from a saved report.")
    list_parser.add_argument("report_path", type=Path)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one review case.")
    inspect_parser.add_argument("report_path", type=Path)
    inspect_parser.add_argument("review_case_id", type=str)

    resolve_parser = subparsers.add_parser("resolve", help="Apply a human review decision.")
    resolve_parser.add_argument("report_path", type=Path)
    resolve_parser.add_argument("review_case_id", type=str)
    resolve_parser.add_argument(
        "--decision",
        choices=[item.value for item in HumanReviewDecision],
        required=True,
    )
    resolve_parser.add_argument("--reviewer-id", type=str, default=None)
    resolve_parser.add_argument("--output-report-dir", type=Path, required=True)

    parser.add_argument(
        "--ingestion-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
    )
    parser.add_argument(
        "--entity-resolution-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "entity_resolution.yaml",
    )
    return parser.parse_args()


def _load_report(report_path: Path) -> ReviewWorkflow:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    from human_review.models import ReviewAuditEntry, ReviewCase, ReviewWorkflowState

    cases = []
    for item in payload["workflow_state"]["cases"]:
        from entity_resolution.models import MatchDecisionType, RecordPair
        from human_review.models import (
            ReviewBlockingReason,
            ReviewConflictEvidence,
            ReviewEvidence,
            ReviewResolution,
            ReviewStatus,
        )

        resolution = None
        if item.get("resolution") is not None:
            resolution_payload = item["resolution"]
            resolution = ReviewResolution(
                review_case_id=item["review_case_id"],
                human_decision=HumanReviewDecision(resolution_payload["human_decision"]),
                reviewer_id=resolution_payload.get("reviewer_id"),
                resolution_sequence=resolution_payload["resolution_sequence"],
                machine_decision=MatchDecisionType(resolution_payload["machine_decision"]),
                machine_reason=resolution_payload["machine_reason"],
                downstream_action=resolution_payload["downstream_action"],
            )
        cases.append(
            ReviewCase(
                review_case_id=item["review_case_id"],
                pair=RecordPair.ordered(item["record_a_id"], item["record_b_id"]),
                record_ids=(item["record_a_id"], item["record_b_id"]),
                machine_decision=MatchDecisionType(item["machine_decision"]),
                machine_score=item["machine_score"],
                auto_match_threshold=item["auto_match_threshold"],
                review_threshold=item["review_threshold"],
                machine_reason=item["machine_reason"],
                blocking_reasons=tuple(
                    ReviewBlockingReason(**reason) for reason in item["blocking_reasons"]
                ),
                supporting_evidence=tuple(
                    ReviewEvidence(**evidence) for evidence in item["supporting_evidence"]
                ),
                conflicting_evidence=tuple(
                    ReviewConflictEvidence(**conflict) for conflict in item["conflicting_evidence"]
                ),
                missing_evidence_notes=tuple(item["missing_evidence_notes"]),
                machine_readable_reasons=tuple(item["machine_readable_reasons"]),
                human_summary=item["human_summary"],
                status=ReviewStatus(item["status"]),
                resolution=resolution,
            )
        )
    audit_trail = tuple(
        ReviewAuditEntry(**entry) for entry in payload["workflow_state"]["audit_trail"]
    )
    state = ReviewWorkflowState(
        cases=tuple(cases),
        audit_trail=audit_trail,
        next_resolution_sequence=payload["workflow_state"]["next_resolution_sequence"],
    )
    return ReviewWorkflow(state)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "generate":
            ingestion_config = load_ingestion_config(args.ingestion_config)
            resolution_config = load_entity_resolution_config(args.entity_resolution_config)
            parsed = parse_file(args.input_path, config=ingestion_config)
            quality = run_quality_pipeline(parsed)
            records = build_entity_records_from_quality_result(parsed, quality)
            resolution = resolve_entities(records, config=resolution_config)
            workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
            outcome = workflow.to_outcome()
            report_path = write_review_reports(outcome, output_directory=args.report_dir)
            print(f"Generated {len(outcome.cases)} review cases.")
            print(f"Report: {report_path}")
            return 0

        workflow = _load_report(args.report_path)
        if args.command == "list":
            for case in workflow.list_cases():
                print(
                    f"{case.review_case_id}\t{case.status.value}\t{case.pair.record_a_id}\t{case.pair.record_b_id}"
                )
            return 0

        if args.command == "inspect":
            case = workflow.get_case(args.review_case_id)
            print(json.dumps(case.to_dict(), indent=2, ensure_ascii=False))
            return 0

        if args.command == "resolve":
            workflow.resolve_case(
                args.review_case_id,
                decision=HumanReviewDecision(args.decision),
                reviewer_id=args.reviewer_id,
            )
            report_path = write_review_reports(
                workflow.to_outcome(),
                output_directory=args.output_report_dir,
            )
            print(f"Resolved {args.review_case_id} as {args.decision}.")
            print(f"Report: {report_path}")
            return 0

        return 1
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Human review command failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
