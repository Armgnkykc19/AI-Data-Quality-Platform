#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset.build import build_from_config_path  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the complete golden dataset (clean base + corruptions)."
    )
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
        result = build_from_config_path(args.config)
    except Exception as exc:
        print(f"Dataset build failed: {exc}")
        return 1

    print("Golden dataset build complete")
    print(f"Output: {result.output_base}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Corruption types: {len(result.corruption_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
