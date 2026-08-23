#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from profiling.profiler import profile_dataset  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402
from schema_mapping.apply import apply_mapping_plan  # noqa: E402
from schema_mapping.config import load_schema_mapping_config  # noqa: E402
from schema_mapping.engine import build_mapping_plan  # noqa: E402
from schema_mapping.reporting import write_mapping_reports  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map source columns to canonical schema with explainable evidence."
    )
    parser.add_argument("input_path", type=Path, help="Path to input CSV or XLSX file.")
    parser.add_argument(
        "--ingestion-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
    )
    parser.add_argument(
        "--schema-mapping-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "schema_mapping.yaml",
    )
    parser.add_argument(
        "--validation-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "validation.yaml",
    )
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply AUTO_MAP-safe mappings and write canonical-shaped output.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output path for canonical CSV when --apply is used.",
    )
    return parser.parse_args()


def _print_mapping_table(plan) -> None:
    print("Source Column    Canonical Field    Decision    Score")
    print("-----------------------------------------------------")
    for mapping in plan.column_mappings:
        canonical = mapping.canonical_field or "-"
        print(
            f"{mapping.source_column:<16} "
            f"{canonical:<18} "
            f"{mapping.decision.value:<10} "
            f"{mapping.score:.2f}"
        )


def _write_canonical_csv(path: Path, applied) -> None:
    from schema_mapping.config import load_schema_mapping_config

    config = load_schema_mapping_config()
    fieldnames = list(config.mappable_fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in applied.records:
            writer.writerow(record.canonical_values)


def main() -> int:
    args = parse_args()
    try:
        ingestion_config = load_ingestion_config(args.ingestion_config)
        mapping_config = load_schema_mapping_config(args.schema_mapping_config)
        if args.report_dir is not None:
            mapping_config = replace(
                mapping_config,
                report_output_directory=args.report_dir,
            )

        parsed = parse_file(args.input_path, config=ingestion_config)
        profile = profile_dataset(parsed, ingestion_config)
        plan = build_mapping_plan(parsed, profile=profile, config=mapping_config)
        report_path = write_mapping_reports(plan, mapping_config)

        print("Schema Mapping")
        print("--------------")
        _print_mapping_table(plan)
        print()
        summary = {
            "file": str(args.input_path),
            "auto_map": plan.summary.auto_map_count,
            "review": plan.summary.review_count,
            "unmapped": plan.summary.unmapped_count,
            "conflict": plan.summary.conflict_count,
            "missing_canonical_fields": list(plan.summary.missing_canonical_fields),
            "status": "ok",
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if report_path is not None:
            print()
            print(f"Report: {report_path}")

        if args.apply:
            applied = apply_mapping_plan(parsed, plan, config=mapping_config)
            output_path = args.output_path or (
                PROJECT_ROOT
                / "datasets"
                / "generated"
                / "schema-mapping"
                / f"{args.input_path.stem}.canonical.csv"
            )
            _write_canonical_csv(output_path, applied)

            quality = run_quality_pipeline(parsed)
            apply_summary = {
                "records_processed": applied.total_records,
                "auto_map_fields_applied": list(applied.auto_map_fields_applied),
                "review_fields_skipped": list(applied.review_fields_skipped),
                "unmapped_source_columns": list(applied.unmapped_source_columns),
                "missing_canonical_fields": list(applied.missing_canonical_fields),
                "canonical_output": str(output_path),
                "post_mapping_validation_errors": quality.post_validation_summary.error_count,
                "quality_pipeline_transformations": quality.total_transformations,
            }
            print()
            print("Mapping Application")
            print("-------------------")
            print(json.dumps(apply_summary, indent=2, ensure_ascii=False))

        return 0
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Schema mapping failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
