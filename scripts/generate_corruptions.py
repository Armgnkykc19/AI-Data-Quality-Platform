#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset.build import build_golden_dataset  # noqa: E402
from dataset.config import (  # noqa: E402
    DEFAULT_CORRUPTIONS_CONFIG,
    load_corruptions_config,
    load_dataset_config,
)


def _read_canonical_records(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply controlled corruptions to an existing clean-base dataset."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CORRUPTIONS_CONFIG,
        help="Path to corruptions configuration YAML.",
    )
    parser.add_argument(
        "--dataset-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "dataset.yaml",
        help="Path to dataset configuration YAML.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        load_corruptions_config(args.config)
        dataset_config = load_dataset_config(args.dataset_config)
        clean_path = dataset_config.output_base / "clean" / "canonical.csv"
        if not clean_path.exists():
            print(
                "Clean base not found. Run scripts/generate_dataset.py first "
                f"or use scripts/build_golden_dataset.py. Missing: {clean_path}"
            )
            return 1

        _read_canonical_records(clean_path)
        result = build_golden_dataset(
            dataset_config=dataset_config,
            corruptions_config_path=args.config,
        )
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"Corruption generation failed: {exc}")
        return 1

    print("Corruption generation complete")
    print(f"Output: {result.output_base}")
    print(f"Corruption events: {result.ground_truth.expected_counts['corruption_events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
