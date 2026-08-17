from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.config import load_ingestion_config
from ingestion.models import ParsedDataset, ParsedRow, SourceMetadata
from profiling.profiler import profile_dataset
from schema_mapping.engine import build_mapping_plan
from schema_mapping.failure_analysis import classify_failure

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = (
    PROJECT_ROOT / "evaluation" / "fixtures" / "schema_mapping_benchmark_cases.json"
)


@dataclass
class SchemaMappingBenchmarkResult:
    labeled_case_count: int = 0
    labeled_column_count: int = 0
    correct_mappings: int = 0
    incorrect_mappings: int = 0
    missed_mappings: int = 0
    auto_map_total: int = 0
    auto_map_correct: int = 0
    auto_map_incorrect: int = 0
    expected_review_count: int = 0
    actual_review_count: int = 0
    correct_review_routing: int = 0
    missed_review_cases: int = 0
    expected_unmapped_count: int = 0
    actual_unmapped_count: int = 0
    correct_unmapped: int = 0
    failures_by_category: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    ran_successfully: bool = True
    error_message: str | None = None

    @property
    def precision(self) -> float:
        predicted = self.correct_mappings + self.incorrect_mappings
        if predicted == 0:
            return 1.0
        return self.correct_mappings / predicted

    @property
    def recall(self) -> float:
        expected_positive = self.correct_mappings + self.missed_mappings
        if expected_positive == 0:
            return 1.0
        return self.correct_mappings / expected_positive

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def mapping_accuracy(self) -> float:
        if self.labeled_column_count == 0:
            return 1.0
        return self.correct_mappings / self.labeled_column_count

    @property
    def auto_map_precision(self) -> float:
        if self.auto_map_total == 0:
            return 1.0
        return self.auto_map_correct / self.auto_map_total

    @property
    def review_routing_recall(self) -> float:
        if self.expected_review_count == 0:
            return 1.0
        return self.correct_review_routing / self.expected_review_count

    @property
    def passed(self) -> bool:
        return self.incorrect_mappings == 0 and self.missed_mappings == 0


def _build_parsed_dataset(headers: list[str], rows: list[list[str]]) -> ParsedDataset:
    parsed = ParsedDataset(
        metadata=SourceMetadata(
            path="benchmark://inline",
            format="csv",
            size_bytes=0,
        ),
        headers=headers,
    )
    for index, raw_row in enumerate(rows, start=2):
        values = {
            headers[col_index]: (raw_row[col_index] if col_index < len(raw_row) else None)
            for col_index in range(len(headers))
        }
        parsed.rows.append(ParsedRow(row_number=index, values=values))
    parsed.finalize_accounting()
    return parsed


def load_schema_mapping_benchmark_cases(
    cases_path: Path = DEFAULT_CASES_PATH,
) -> list[dict[str, object]]:
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("schema mapping benchmark cases must be a list")
    return data


def run_schema_mapping_benchmark(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
) -> SchemaMappingBenchmarkResult:
    result = SchemaMappingBenchmarkResult()
    try:
        cases = load_schema_mapping_benchmark_cases(cases_path)
        ingestion_config = load_ingestion_config()
        result.labeled_case_count = len(cases)

        for case in cases:
            headers = [str(item) for item in case["headers"]]
            rows = case.get("rows", [])
            if not isinstance(rows, list):
                continue
            parsed = _build_parsed_dataset(headers, rows)
            profile = profile_dataset(parsed, ingestion_config)
            plan = build_mapping_plan(parsed, profile=profile)
            expected = case.get("expected", {})
            if not isinstance(expected, dict):
                continue

            mapping_by_source = {
                item.source_column: item for item in plan.column_mappings
            }

            for source_column, expectation in expected.items():
                if not isinstance(expectation, dict):
                    continue
                result.labeled_column_count += 1
                expected_field = expectation.get("canonical_field")
                expected_decision = str(expectation.get("decision", "UNMAPPED"))
                actual = mapping_by_source.get(str(source_column))
                if actual is None:
                    result.missed_mappings += 1
                    result.messages.append(
                        f"missed:{case['case_id']}:{source_column}:missing_column"
                    )
                    continue

                actual_field = actual.canonical_field
                actual_decision = actual.decision.value

                if expected_decision == "REVIEW":
                    result.expected_review_count += 1
                    if actual_decision == "REVIEW":
                        result.correct_review_routing += 1
                        result.correct_mappings += 1
                    else:
                        result.missed_review_cases += 1
                        result.missed_mappings += 1
                        result.messages.append(
                            f"review_miss:{case['case_id']}:{source_column}:"
                            f"expected=REVIEW,got={actual_decision}"
                        )
                    continue

                if expected_decision == "UNMAPPED":
                    result.expected_unmapped_count += 1
                    if actual_decision == "UNMAPPED":
                        result.correct_unmapped += 1
                        result.correct_mappings += 1
                    else:
                        result.incorrect_mappings += 1
                        result.messages.append(
                            f"false_map:{case['case_id']}:{source_column}:"
                            f"expected=UNMAPPED,got={actual_decision}"
                        )
                    continue

                if expected_decision == "CONFLICT":
                    if actual_decision == "CONFLICT":
                        result.correct_mappings += 1
                    else:
                        result.incorrect_mappings += 1
                        result.messages.append(
                            f"collision_miss:{case['case_id']}:{source_column}:"
                            f"expected=CONFLICT,got={actual_decision}"
                        )
                    continue

                if actual_decision == "AUTO_MAP":
                    result.auto_map_total += 1
                    if actual_field == expected_field:
                        result.auto_map_correct += 1
                        result.correct_mappings += 1
                    else:
                        result.auto_map_incorrect += 1
                        result.incorrect_mappings += 1
                        result.messages.append(
                            f"false_auto_map:{case['case_id']}:{source_column}:"
                            f"expected={expected_field},got={actual_field}"
                        )
                elif actual_field == expected_field and actual_decision == expected_decision:
                    result.correct_mappings += 1
                else:
                    result.missed_mappings += 1
                    result.messages.append(
                        f"missed:{case['case_id']}:{source_column}:"
                        f"expected={expected_field}/{expected_decision},"
                        f"got={actual_field}/{actual_decision}"
                    )

                failure = classify_failure(
                    expected_field=str(expected_field) if expected_field else None,
                    expected_decision=expected_decision,
                    actual_field=actual_field,
                    actual_decision=actual_decision,
                )
                if failure is not None:
                    result.failures_by_category[failure.value] = (
                        result.failures_by_category.get(failure.value, 0) + 1
                    )

                if actual_decision == "REVIEW":
                    result.actual_review_count += 1
                if actual_decision == "UNMAPPED":
                    result.actual_unmapped_count += 1

    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)

    return result
