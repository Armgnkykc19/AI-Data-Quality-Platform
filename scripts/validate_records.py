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
from validation.config import load_validation_config  # noqa: E402
from validation.pipeline import validate_parsed_dataset  # noqa: E402
from validation.reporting import write_validation_reports  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate parsed CSV/XLSX records.")
    parser.add_argument("input_path", type=Path, help="Path to input CSV or XLSX file.")
    parser.add_argument(
        "--ingestion-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
    )
    parser.add_argument(
        "--validation-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "validation.yaml",
    )
    parser.add_argument("--report-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ingestion_config = load_ingestion_config(args.ingestion_config)
        validation_config = load_validation_config(args.validation_config)
        if args.report_dir is not None:
            validation_config = replace(
                validation_config,
                report_output_directory=args.report_dir,
            )

        parsed = parse_file(args.input_path, config=ingestion_config)
        result = validate_parsed_dataset(parsed, config=validation_config)
        report_path = write_validation_reports(result, validation_config)

        summary = {
            "file": str(args.input_path),
            "records": result.summary.total_records,
            "valid_records": result.summary.valid_records,
            "invalid_records": result.summary.invalid_records,
            "errors": result.summary.error_count,
            "warnings": result.summary.warning_count,
            "status": "ok" if result.summary.invalid_records == 0 else "invalid",
        }
        print("Record Validation")
        print("------------------")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if report_path is not None:
            print()
            print(f"Report: {report_path}")
        return 0 if result.summary.error_count == 0 else 1
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Validation failed: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
