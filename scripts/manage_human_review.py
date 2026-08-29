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
from human_review.errors import HumanReviewError, HumanReviewReportError  # noqa: E402
from human_review.models import HumanReviewDecision  # noqa: E402
from human_review.reporting import (  # noqa: E402
    load_human_review_report,
    write_review_reports,
)
from human_review.workflow import ReviewWorkflow  # noqa: E402
from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and resolve deterministic human review cases for entity resolution.",
        epilog=(
            "Exit codes: 0 success, 1 usage, 2 ingestion, "
            "3 report/IO error, 4 human-review policy rejection."
        ),
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


def _load_records(input_path: Path, ingestion_config):
    parsed = parse_file(input_path, config=ingestion_config)
    quality = run_quality_pipeline(parsed)
    return build_entity_records_from_quality_result(parsed, quality)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "generate":
            ingestion_config = load_ingestion_config(args.ingestion_config)
            resolution_config = load_entity_resolution_config(args.entity_resolution_config)
            records = _load_records(args.input_path, ingestion_config)
            resolution = resolve_entities(records, config=resolution_config)
            workflow = ReviewWorkflow(generate_review_cases(resolution, config=resolution_config))
            outcome = workflow.to_outcome()
            report_path = write_review_reports(
                outcome,
                output_directory=args.report_dir,
                entity_records=records,
                resolution=resolution,
                entity_resolution_config_path=args.entity_resolution_config,
            )
            print(f"Generated {len(outcome.cases)} review cases.")
            print(f"Report: {report_path}")
            return 0

        loaded = load_human_review_report(args.report_path)
        workflow = ReviewWorkflow(loaded.outcome.workflow_state)
        if args.command == "list":
            for case in workflow.list_cases():
                print(
                    f"{case.review_case_id}\t{case.status.value}\t"
                    f"{case.pair.record_a_id}\t{case.pair.record_b_id}"
                )
            return 0

        if args.command == "inspect":
            case = workflow.get_case(args.review_case_id)
            print(json.dumps(case.to_dict(), indent=2, ensure_ascii=False))
            return 0

        if args.command == "resolve":
            config_path = (
                Path(loaded.entity_resolution_config_path)
                if loaded.entity_resolution_config_path
                else args.entity_resolution_config
            )
            resolution_config = load_entity_resolution_config(config_path)
            decision = HumanReviewDecision(args.decision)
            records_by_id = {record.record_id: record for record in loaded.entity_records}
            if not records_by_id:
                raise HumanReviewReportError(
                    "Persisted review report is missing entity records required for authorization."
                )
            if decision == HumanReviewDecision.MATCH:
                case = workflow.get_case(args.review_case_id)
                if (
                    case.pair.record_a_id not in records_by_id
                    or case.pair.record_b_id not in records_by_id
                ):
                    raise HumanReviewReportError(
                        "Persisted review report cannot reconstruct MATCH authorization "
                        "context for the reviewed records."
                    )
            workflow.resolve_case(
                args.review_case_id,
                decision=decision,
                reviewer_id=args.reviewer_id,
                resolution=loaded.resolution,
                records_by_id=records_by_id,
                entity_resolution_config=resolution_config,
            )
            report_path = write_review_reports(
                workflow.to_outcome(),
                output_directory=args.output_report_dir,
                entity_records=loaded.entity_records,
                resolution=loaded.resolution,
                entity_resolution_config_path=config_path,
            )
            print(f"Resolved {args.review_case_id} as {args.decision}.")
            print(f"Report: {report_path}")
            return 0

        return 1
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except HumanReviewReportError as exc:
        print(f"Review report error: {exc}")
        return 3
    except HumanReviewError as exc:
        print(f"Human review rejected: {exc}")
        return 4
    except (OSError, ValueError, KeyError) as exc:
        print(f"Human review command failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
