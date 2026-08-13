#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset.validation import validate_dataset  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated golden dataset.")
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to generated dataset directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_dataset(args.dataset)

    print("Dataset Validation")
    print("------------------")
    for issue in result.issues:
        print(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")

    if result.passed:
        print("Overall Status: PASS")
        return 0

    print("Overall Status: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
