from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from normalization.config import load_normalization_config
from normalization.engine import NormalizationEngine
from validation.models import NormalizationEligibility

NORMALIZABLE_CORRUPTION_TYPES = frozenset(
    {
        "whitespace",
        "phone_format",
    }
)

NON_NORMALIZABLE_CORRUPTION_TYPES = frozenset(
    {
        "typo",
        "email_corruption",
        "unicode_turkish",
        "missing_value",
        "field_conflict",
        "duplicate",
        "case_change",
        "punctuation",
        "abbreviation",
    }
)

CORRUPTION_TYPE_SCOPE: dict[str, str] = {
    "whitespace": "SUPPORTED_DETERMINISTIC",
    "phone_format": "SUPPORTED_DETERMINISTIC",
    "typo": "INTENTIONALLY_NOT_NORMALIZED",
    "email_corruption": "INTENTIONALLY_NOT_NORMALIZED",
    "unicode_turkish": "INTENTIONALLY_NOT_NORMALIZED",
    "missing_value": "INTENTIONALLY_NOT_NORMALIZED",
    "field_conflict": "INTENTIONALLY_NOT_NORMALIZED",
    "duplicate": "INTENTIONALLY_NOT_NORMALIZED",
    "case_change": "INTENTIONALLY_NOT_NORMALIZED",
    "punctuation": "INTENTIONALLY_NOT_NORMALIZED",
    "abbreviation": "INTENTIONALLY_NOT_NORMALIZED",
}

CORRUPTION_TYPE_SCOPE_REASONING: dict[str, str] = {
    "whitespace": "Deterministic trim/collapse rules recover canonical spacing safely.",
    "phone_format": "TR E.164 conversion is deterministic when digit structure is recoverable.",
    "typo": "Character substitution requires guessing the intended original token.",
    "email_corruption": "Mailbox repair can invent addresses or lose semantic identity.",
    "unicode_turkish": "Turkish character restoration requires semantic inference beyond trim/NFC.",
    "missing_value": "Absent values cannot be reconstructed without external evidence.",
    "field_conflict": "Conflicting merged values require entity-resolution disambiguation.",
    "duplicate": "Duplicate collapse belongs to later merge/entity-resolution scope.",
    "case_change": (
        "Case-only corruption may be deterministic for some fields but is excluded "
        "to avoid unsafe mixed-field guessing."
    ),
    "punctuation": (
        "Punctuation repair can change meaning and is deferred to future semantic rules."
    ),
    "abbreviation": "Expansion requires domain knowledge and is intentionally out of scope.",
}


@dataclass
class NormalizationBenchmarkResult:
    expected_transformations: int = 0
    correct_transformations: int = 0
    incorrect_transformations: int = 0
    missed_transformations: int = 0
    unnecessary_transformations: int = 0
    skipped_non_normalizable: int = 0
    failures_by_category: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    @property
    def normalization_accuracy(self) -> float:
        if self.expected_transformations == 0:
            return 1.0
        return self.correct_transformations / self.expected_transformations

    @property
    def passed(self) -> bool:
        return self.incorrect_transformations == 0 and self.missed_transformations == 0


def _load_corruption_events(dataset_path: Path) -> list[dict[str, object]]:
    log_path = dataset_path / "ground_truth" / "corruption_log.jsonl"
    if not log_path.exists():
        return []
    events: list[dict[str, object]] = []
    with log_path.open("r", encoding="utf-8") as file:
        for line in file:
            events.append(json.loads(line))
    return events


def run_normalization_benchmark(*, dataset_path: Path) -> NormalizationBenchmarkResult:
    engine = NormalizationEngine(load_normalization_config())
    result = NormalizationBenchmarkResult()

    for event in _load_corruption_events(dataset_path):
        corruption_type = str(event.get("corruption_type", ""))
        field_name = str(event.get("field_name", ""))
        before_value = event.get("before_value")
        after_value = event.get("after_value")

        if before_value is None or after_value is None:
            continue
        if not isinstance(before_value, str) or not isinstance(after_value, str):
            continue
        if field_name not in {
            "first_name",
            "last_name",
            "email",
            "phone",
            "company",
            "city",
            "district",
            "address",
        }:
            continue

        if corruption_type in NON_NORMALIZABLE_CORRUPTION_TYPES:
            result.skipped_non_normalizable += 1
            normalized, _ = engine.normalize_field(
                field_name,
                after_value,
                original_value=after_value,
                normalization_eligibility=NormalizationEligibility.NOT_APPLICABLE,
            )
            if normalized != after_value:
                result.unnecessary_transformations += 1
                result.failures_by_category["UNNECESSARY_TRANSFORMATION"] = (
                    result.failures_by_category.get("UNNECESSARY_TRANSFORMATION", 0) + 1
                )
                result.messages.append(
                    f"unnecessary:{corruption_type}:{field_name}:{after_value}->{normalized}"
                )
            continue

        if corruption_type not in NORMALIZABLE_CORRUPTION_TYPES:
            continue

        result.expected_transformations += 1
        normalized, transformations = engine.normalize_field(
            field_name,
            after_value,
            original_value=after_value,
            normalization_eligibility=NormalizationEligibility.SAFE,
        )
        if normalized == before_value:
            result.correct_transformations += 1
            result.messages.append(f"correct:{corruption_type}:{field_name}")
        elif normalized == after_value:
            result.missed_transformations += 1
            result.failures_by_category["MISSED_TRANSFORMATION"] = (
                result.failures_by_category.get("MISSED_TRANSFORMATION", 0) + 1
            )
            result.messages.append(f"missed:{corruption_type}:{field_name}:{after_value}")
        else:
            result.incorrect_transformations += 1
            result.failures_by_category["INCORRECT_TRANSFORMATION"] = (
                result.failures_by_category.get("INCORRECT_TRANSFORMATION", 0) + 1
            )
            result.messages.append(
                f"incorrect:{corruption_type}:{field_name}:expected={before_value},got={normalized}"
            )

    if result.expected_transformations == 0:
        result.messages.append("normalization_benchmark:no_normalizable_events")

    return result
