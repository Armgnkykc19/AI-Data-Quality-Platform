#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset.config import load_dataset_config, load_schema_config  # noqa: E402
from dataset.generator.clean_base import generate_clean_base  # noqa: E402
from dataset.generator.sources import write_canonical_csv  # noqa: E402
from dataset.manifest import write_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the canonical clean-base dataset.")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "dataset.yaml",
        help="Path to dataset configuration YAML.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_dataset_config(args.config)
        schema = load_schema_config(config.schema_path)
        records = generate_clean_base(
            seed=config.seed,
            record_count=config.record_count,
        )
        output_base = config.output_base
        clean_path = output_base / "clean" / "canonical.csv"
        write_canonical_csv(clean_path, records)
        write_json(output_base / "schema" / "canonical_schema.json", schema)
        write_json(
            output_base / "config" / "dataset_config.json",
            config.raw,
        )
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"Clean base generation failed: {exc}")
        return 1

    print("Clean base generation complete")
    print(f"Records: {len(records)}")
    print(f"Output: {clean_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
