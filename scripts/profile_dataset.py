#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from profiling.profiler import profile_dataset  # noqa: E402
from profiling.reporting import (  # noqa: E402
    write_json_profile_report,
    write_markdown_profile_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile an input CSV or XLSX dataset.")
    parser.add_argument("input_path", type=Path, help="Path to input CSV or XLSX file.")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
        help="Path to ingestion configuration YAML.",
    )
    parser.add_argument(
        "--worksheet",
        type=str,
        default=None,
        help="Optional worksheet name for XLSX files.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Optional report output directory override.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_ingestion_config(args.config)
        parsed = parse_file(args.input_path, config=config, worksheet_name=args.worksheet)
        profile = profile_dataset(parsed, config)

        report_dir = args.report_dir or config.report_output_directory
        if config.report_json:
            write_json_profile_report(profile, report_dir / "profile.json")
        if config.report_markdown:
            write_markdown_profile_report(profile, report_dir / "profile.md")

        summary = {
            "file": str(args.input_path),
            "format": profile.format,
            "accepted_rows": profile.accepted_rows,
            "rejected_rows": profile.rejected_rows,
            "columns": profile.column_count,
            "encoding": profile.encoding,
            "delimiter": profile.delimiter,
            "worksheet": profile.worksheet,
            "status": profile.status,
        }
        print("Dataset Profiling")
        print("------------------")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print()
        print(f"Reports: {report_dir}")
        return 0
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"Profiling failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
