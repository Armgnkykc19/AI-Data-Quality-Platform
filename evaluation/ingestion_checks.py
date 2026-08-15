from __future__ import annotations

from pathlib import Path

from ingestion.config import load_ingestion_config
from ingestion.errors import IngestionError
from ingestion.parser import parse_file
from profiling.profiler import profile_dataset


def run_ingestion_smoke_checks(*, malformed_dir: Path) -> tuple[bool, list[str]]:
    messages: list[str] = []
    config = load_ingestion_config()
    checks = {
        "semicolon_delimiter.csv": {"accepted": 1, "rejected": 0},
        "utf8_turkish.csv": {"accepted": 1, "rejected": 0},
        "missing_column_row.csv": {"accepted": 0, "rejected": 1},
        "extra_column_row.csv": {"accepted": 0, "rejected": 1},
        "header_only.csv": {"accepted": 0, "rejected": 0},
        "broken_quotes.csv": {"accepted": 0, "rejected": 1},
    }

    passed = True
    for filename, expected in checks.items():
        path = malformed_dir / filename
        if not path.exists():
            messages.append(f"ingestion_smoke:missing_fixture:{filename}")
            passed = False
            continue
        try:
            parsed = parse_file(path, config=config)
            accounting = parsed.accounting
            assert accounting is not None
            if (
                accounting.accepted_rows != expected["accepted"]
                or accounting.rejected_rows != expected["rejected"]
            ):
                messages.append(
                    f"ingestion_smoke:accounting_mismatch:{filename}:"
                    f"accepted={accounting.accepted_rows},"
                    f"rejected={accounting.rejected_rows}"
                )
                passed = False
            else:
                messages.append(f"ingestion_smoke:PASS:{filename}")
        except IngestionError as exc:
            messages.append(f"ingestion_smoke:unexpected_error:{filename}:{exc.code}")
            passed = False

    fatal_cases = {
        "empty_file.csv": "empty_file",
        "duplicate_header.csv": "duplicate_header",
    }
    for filename, expected_code in fatal_cases.items():
        path = malformed_dir / filename
        try:
            parse_file(path, config=config)
            messages.append(f"ingestion_smoke:expected_error_missing:{filename}")
            passed = False
        except IngestionError as exc:
            if exc.code == expected_code:
                messages.append(f"ingestion_smoke:PASS:{filename}:{exc.code}")
            else:
                messages.append(
                    f"ingestion_smoke:error_code_mismatch:{filename}:{exc.code}"
                )
                passed = False

    latin5_path = malformed_dir / "latin5_turkish.csv"
    if latin5_path.exists():
        parsed = parse_file(latin5_path, config=config)
        profile = profile_dataset(parsed, config)
        if profile.accepted_rows != 1:
            messages.append("ingestion_smoke:latin5_accounting_fail")
            passed = False
        else:
            messages.append("ingestion_smoke:PASS:latin5_turkish.csv")

    if passed:
        messages.append("ingestion_contract:PASS")
    else:
        messages.append("ingestion_contract:FAIL")
    return passed, messages
