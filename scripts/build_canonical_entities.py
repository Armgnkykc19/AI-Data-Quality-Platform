#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from entity_resolution.config import load_entity_resolution_config  # noqa: E402
from entity_resolution.engine import resolve_entities  # noqa: E402
from entity_resolution.records import build_entity_records_from_quality_result  # noqa: E402
from human_review.errors import HumanReviewError, HumanReviewReportError  # noqa: E402
from human_review.reporting import load_human_review_report  # noqa: E402
from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402
from survivorship.config import load_survivorship_config  # noqa: E402
from survivorship.engine import build_canonical_entities  # noqa: E402
from survivorship.reporting import write_survivorship_reports  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build canonical entities from entity resolution clusters using "
            "deterministic survivorship rules."
        )
    )
    parser.add_argument(
        "input_paths",
        nargs="+",
        type=Path,
        help="One or more input CSV/XLSX files containing source records.",
    )
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
    parser.add_argument(
        "--survivorship-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "survivorship.yaml",
    )
    parser.add_argument(
        "--human-review-report",
        type=Path,
        default=None,
        help="Optional validated human-review JSON outcome to apply before survivorship.",
    )
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--inspect-entity",
        type=str,
        default=None,
        help="Inspect a canonical entity by entity_id.",
    )
    return parser.parse_args()


def _load_entity_records(input_paths: list[Path], ingestion_config) -> list:
    records = []
    for input_path in sorted(input_paths):
        parsed = parse_file(input_path, config=ingestion_config)
        quality = run_quality_pipeline(parsed)
        records.extend(build_entity_records_from_quality_result(parsed, quality))
    return records


def _print_summary(result) -> None:
    summary = result.summary
    print("Survivorship / Canonical Entity Construction")
    print("--------------------------------------------")
    print(f"Input records: {summary.input_record_count}")
    print(f"Resolution clusters: {summary.cluster_count}")
    print(f"Canonical entities: {summary.canonical_entity_count}")
    print(f"Merged entities: {summary.merged_entity_count}")
    print(f"Singleton entities: {summary.singleton_entity_count}")
    print(f"Preserved field conflicts: {summary.preserved_conflict_count}")
    print(f"Review-excluded records: {summary.review_excluded_record_count}")
    print()
    payload = {
        "input_records": summary.input_record_count,
        "clusters": summary.cluster_count,
        "canonical_entities": summary.canonical_entity_count,
        "merged_entities": summary.merged_entity_count,
        "singleton_entities": summary.singleton_entity_count,
        "preserved_conflicts": summary.preserved_conflict_count,
        "review_excluded_records": summary.review_excluded_record_count,
        "status": "ok",
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    args = parse_args()
    try:
        ingestion_config = load_ingestion_config(args.ingestion_config)
        resolution_config = load_entity_resolution_config(args.entity_resolution_config)
        survivorship_config = load_survivorship_config(args.survivorship_config)
        if args.report_dir is not None:
            survivorship_config = replace(
                survivorship_config,
                report_output_directory=args.report_dir,
            )

        entity_records = _load_entity_records(args.input_paths, ingestion_config)
        resolution = resolve_entities(
            entity_records,
            source_label=",".join(str(path) for path in args.input_paths),
            config=resolution_config,
        )
        human_review_outcome = None
        if args.human_review_report is not None:
            loaded = load_human_review_report(args.human_review_report)
            current_ids = {record.record_id for record in resolution.records}
            missing = [
                pair
                for pair in loaded.outcome.resolved_match_pairs()
                if pair.record_a_id not in current_ids or pair.record_b_id not in current_ids
            ]
            if missing:
                raise HumanReviewReportError(
                    "Human MATCH pairs reference records that are not in the current "
                    "canonical inputs: "
                    + ", ".join(f"{pair.record_a_id}/{pair.record_b_id}" for pair in missing)
                )
            human_review_outcome = loaded.outcome

        result = build_canonical_entities(
            resolution,
            config=survivorship_config,
            entity_resolution_config=resolution_config,
            human_review_outcome=human_review_outcome,
        )

        if args.inspect_entity is not None:
            entity = next(
                (item for item in result.entities if item.entity_id == args.inspect_entity),
                None,
            )
            if entity is None:
                print(f"Canonical entity not found: {args.inspect_entity}")
                return 4
            print(json.dumps(entity.to_dict(), indent=2, ensure_ascii=False))
            return 0

        _print_summary(result)
        report_path = write_survivorship_reports(result, survivorship_config)
        if report_path is not None:
            print()
            print(f"Report: {report_path}")
        return 0
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
        print(f"Survivorship failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
