from __future__ import annotations

import re

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9\s().-]{7,}$")
NUMERIC_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}[./]\d{2}[./]\d{4}$")


def profile_patterns(values: list[str]) -> list[tuple[str, int, int, float]]:
    if not values:
        return []
    sample_size = len(values)
    checks = {
        "email_like": EMAIL_PATTERN,
        "phone_like": PHONE_PATTERN,
        "numeric_like": NUMERIC_PATTERN,
        "date_like": DATE_PATTERN,
    }
    results: list[tuple[str, int, int, float]] = []
    for name, pattern in checks.items():
        matches = sum(1 for value in values if pattern.match(value.strip()))
        results.append((name, matches, sample_size, matches / sample_size))
    return results
