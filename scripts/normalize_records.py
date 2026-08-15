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

from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from normalization.config import load_normalization_config  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402
from record_quality.reporting import write_quality_reports  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize parsed CSV/XLSX records with audit trail."
    )
    parser.add_argument("input_path", type=Path, help="Path to input CSV or XLSX file.")
    parser.add_argument(
        "--ingestion-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
    )
    parser.add_argument(
        "--normalization-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "normalization.yaml",
    )
    parser.add_argument("--report-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ingestion_config = load_ingestion_config(args.ingestion_config)
        normalization_config = load_normalization_config(args.normalization_config)
        if args.report_dir is not None:
            normalization_config = replace(
                normalization_config,
                report_output_directory=args.report_dir,
            )

        parsed = parse_file(args.input_path, config=ingestion_config)
        quality = run_quality_pipeline(
            parsed,
            normalization_config=normalization_config,
        )
        report_path = write_quality_reports(quality, normalization_config)

        summary = {
            "file": str(args.input_path),
            "records_processed": len(quality.records),
            "changed_records": quality.changed_records,
            "total_transformations": quality.total_transformations,
            "pre_validation_errors": quality.pre_validation_summary.error_count,
            "post_validation_errors": quality.post_validation_summary.error_count,
            "status": "ok",
        }
        print("Record Normalization")
        print("--------------------")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if report_path is not None:
            print()
            print(f"Report: {report_path}")
        return 0
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Normalization failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
