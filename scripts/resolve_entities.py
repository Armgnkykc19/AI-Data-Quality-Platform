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
from entity_resolution.models import MatchDecisionType  # noqa: E402
from entity_resolution.records import build_entity_records_from_quality_result  # noqa: E402
from entity_resolution.reporting import write_resolution_reports  # noqa: E402
from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve duplicate entities on canonical mapped and normalized records."
    )
    parser.add_argument("input_path", type=Path, help="Path to input CSV or XLSX file.")
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
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--inspect-pair",
        nargs=2,
        metavar=("RECORD_A", "RECORD_B"),
        default=None,
        help="Inspect a specific record pair by source_record_id.",
    )
    return parser.parse_args()


def _print_summary(result) -> None:
    summary = result.summary
    print("Entity Resolution")
    print("-----------------")
    print(f"Records: {summary.record_count}")
    print(f"Candidate pairs: {summary.candidate_pair_count}")
    print(f"Candidate reduction ratio: {summary.candidate_reduction_ratio:.4f}")
    print(f"AUTO_MATCH: {summary.auto_match_count}")
    print(f"REVIEW: {summary.review_count}")
    print(f"NO_MATCH: {summary.no_match_count}")
    print(f"Clusters: {summary.cluster_count}")
    print()
    payload = {
        "records": summary.record_count,
        "candidate_pairs": summary.candidate_pair_count,
        "auto_match": summary.auto_match_count,
        "review": summary.review_count,
        "no_match": summary.no_match_count,
        "clusters": summary.cluster_count,
        "status": "ok",
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    args = parse_args()
    try:
        ingestion_config = load_ingestion_config(args.ingestion_config)
        resolution_config = load_entity_resolution_config(args.entity_resolution_config)
        if args.report_dir is not None:
            resolution_config = replace(
                resolution_config,
                report_output_directory=args.report_dir,
            )

        parsed = parse_file(args.input_path, config=ingestion_config)
        quality = run_quality_pipeline(parsed)
        entity_records = build_entity_records_from_quality_result(parsed, quality)
        result = resolve_entities(
            entity_records,
            source_label=str(args.input_path),
            config=resolution_config,
        )

        if args.inspect_pair is not None:
            left_id, right_id = args.inspect_pair
            inspected = result.inspect_pair(left_id, right_id)
            if inspected is None:
                print(f"Pair not found in candidate set: {left_id}, {right_id}")
                return 4
            print(json.dumps(
                {
                    "record_a_id": inspected.pair.record_a_id,
                    "record_b_id": inspected.pair.record_b_id,
                    "decision": inspected.decision.value,
                    "score": round(inspected.comparison.score, 6),
                    "reason": inspected.reason,
                    "evidence": [
                        item.evidence_type.value for item in inspected.comparison.evidence
                    ],
                    "conflicts": [
                        item.conflict_type.value for item in inspected.comparison.conflicts
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ))
            return 0

        _print_summary(result)
        report_path = write_resolution_reports(result, resolution_config)
        if report_path is not None:
            print()
            print(f"Report: {report_path}")

        if result.summary.review_count > 0 and result.summary.auto_match_count == 0:
            return 0
        if any(
            decision.decision == MatchDecisionType.AUTO_MATCH for decision in result.decisions
        ):
            return 0
        return 0
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Entity resolution failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
