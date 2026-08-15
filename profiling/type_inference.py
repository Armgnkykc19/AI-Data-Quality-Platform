from __future__ import annotations

import re
from datetime import datetime

INTEGER_PATTERN = re.compile(r"^-?\d+$")
FLOAT_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
BOOLEAN_VALUES = {"true", "false", "yes", "no", "0", "1"}
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y")


def infer_type(values: list[str]) -> tuple[str, str, tuple[str, ...]]:
    if not values:
        return "string", "low", ("no_non_empty_values",)

    notes: list[str] = []
    integer_matches = sum(1 for value in values if INTEGER_PATTERN.match(value))
    float_matches = sum(1 for value in values if FLOAT_PATTERN.match(value))
    boolean_matches = sum(
        1 for value in values if value.strip().lower() in BOOLEAN_VALUES
    )
    date_matches = sum(1 for value in values if _is_date(value))

    total = len(values)
    if integer_matches == total:
        return "integer", "high", tuple(notes)
    if float_matches == total:
        return "float", "high", tuple(notes)
    if boolean_matches == total:
        return "boolean", "high", tuple(notes)
    if date_matches == total:
        return "date", "high", tuple(notes)

    candidates = {
        "integer": integer_matches / total,
        "float": float_matches / total,
        "boolean": boolean_matches / total,
        "date": date_matches / total,
    }
    best_name, best_ratio = max(candidates.items(), key=lambda item: item[1])
    if best_ratio >= 0.95:
        notes.append("mixed_values_below_threshold")
        return best_name, "medium", tuple(notes)

    notes.append("mixed_values")
    return "string", "low", tuple(notes)


def _is_date(value: str) -> bool:
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            continue
    return False
