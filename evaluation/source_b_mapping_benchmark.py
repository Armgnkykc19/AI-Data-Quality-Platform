from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.config import load_ingestion_config
from ingestion.parser import parse_file
from profiling.profiler import profile_dataset
from schema_mapping.engine import build_mapping_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTED_MAPPINGS_PATH = (
    PROJECT_ROOT / "evaluation" / "fixtures" / "source_b_expected_mappings.json"
)

SOURCE_B_SAMPLE_ROWS = [
    {
        "first_name": "Ayse",
        "last_name": "Yilmaz",
        "email": "ayse@example.com",
        "phone": "05321234567",
        "company": "Acme AS",
        "city": "Istanbul",
        "district": "Kadikoy",
        "address": "Test Sokak 1",
    },
    {
        "first_name": "Ali",
        "last_name": "Kaya",
        "email": "ali@example.com",
        "phone": "05329876543",
        "company": "Beta Ltd",
        "city": "Ankara",
        "district": "Cankaya",
        "address": "Ornek Cadde 2",
    },
]


@dataclass
class SourceBMappingBenchmarkResult:
    layout_count: int = 0
    labeled_column_count: int = 0
    correct_mappings: int = 0
    incorrect_mappings: int = 0
    missed_mappings: int = 0
    auto_map_total: int = 0
    auto_map_correct: int = 0
    auto_map_incorrect: int = 0
    expected_unmapped_count: int = 0
    correct_unmapped: int = 0
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
    def passed(self) -> bool:
        return self.incorrect_mappings == 0 and self.missed_mappings == 0


def load_source_b_expected_mappings(
    path: Path = DEFAULT_EXPECTED_MAPPINGS_PATH,
) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    layouts = data.get("layouts", [])
    if not isinstance(layouts, list) or not layouts:
        raise ValueError("source_b_expected_mappings.json must contain layouts")
    return layouts


def _canonical_to_source_row(
    column_set: list[str],
    canonical_row: dict[str, str],
    *,
    layout_index: int,
    row_index: int,
    reverse_map: dict[str, str],
) -> dict[str, str]:
    row: dict[str, str] = {
        "source_record_id": f"source_b-{row_index:06d}",
        "source_name": "source_b",
    }
    for canonical_field, value in canonical_row.items():
        column = reverse_map.get(canonical_field)
        if column and column in column_set:
            row[column] = value

    if "legacy_code" in column_set:
        row["legacy_code"] = f"LEG-{row_index:05d}"
    if "import_batch" in column_set:
        row["import_batch"] = f"BATCH-{layout_index:03d}"
    if "notes" in column_set:
        row["notes"] = "imported record"

    return row


def _build_source_b_csv(
    column_set: list[str],
    *,
    layout_index: int,
    reverse_map: dict[str, str],
) -> str:
    rows = [
        _canonical_to_source_row(
            column_set,
            sample,
            layout_index=layout_index,
            row_index=index,
            reverse_map=reverse_map,
        )
        for index, sample in enumerate(SOURCE_B_SAMPLE_ROWS, start=1)
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=column_set, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def run_source_b_mapping_benchmark(
    *,
    expected_mappings_path: Path = DEFAULT_EXPECTED_MAPPINGS_PATH,
) -> SourceBMappingBenchmarkResult:
    result = SourceBMappingBenchmarkResult()
    try:
        layouts = load_source_b_expected_mappings(expected_mappings_path)
        ingestion_config = load_ingestion_config()
        result.layout_count = len(layouts)

        for layout in layouts:
            layout_index = int(layout["layout_id"])
            column_set = [str(item) for item in layout["column_order"]]
            expected_columns = layout["columns"]
            reverse_map = {
                str(spec["canonical_field"]): column
                for column, spec in expected_columns.items()
                if isinstance(spec, dict)
                and spec.get("canonical_field") is not None
                and spec.get("decision") == "AUTO_MAP"
            }

            csv_text = _build_source_b_csv(
                column_set,
                layout_index=layout_index,
                reverse_map=reverse_map,
            )
            path = PROJECT_ROOT / f"evaluation/artifacts/source_b_layout_{layout_index}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(csv_text, encoding="utf-8")

            parsed = parse_file(path, config=ingestion_config)
            profile = profile_dataset(parsed, ingestion_config)
            plan = build_mapping_plan(parsed, profile=profile)
            mapping_by_source = {item.source_column: item for item in plan.column_mappings}

            for column in column_set:
                expectation = expected_columns.get(column)
                if not isinstance(expectation, dict):
                    continue
                expected_field = expectation.get("canonical_field")
                expected_decision = str(expectation.get("decision", "UNMAPPED"))
                result.labeled_column_count += 1
                actual = mapping_by_source.get(column)
                if actual is None:
                    result.missed_mappings += 1
                    result.messages.append(f"missed:layout_{layout_index}:{column}:missing_column")
                    continue

                if expected_decision == "UNMAPPED":
                    result.expected_unmapped_count += 1
                    if actual.decision.value == "UNMAPPED":
                        result.correct_unmapped += 1
                        result.correct_mappings += 1
                    else:
                        result.incorrect_mappings += 1
                        result.messages.append(
                            f"false_map:layout_{layout_index}:{column}:"
                            f"expected=UNMAPPED,got={actual.decision.value}"
                        )
                    continue

                if actual.decision.value == "AUTO_MAP":
                    result.auto_map_total += 1
                    if actual.canonical_field == expected_field:
                        result.auto_map_correct += 1
                        result.correct_mappings += 1
                    else:
                        result.auto_map_incorrect += 1
                        result.incorrect_mappings += 1
                        result.messages.append(
                            f"false_auto_map:layout_{layout_index}:{column}:"
                            f"expected={expected_field},got={actual.canonical_field}"
                        )
                elif (
                    actual.canonical_field == expected_field
                    and actual.decision.value == expected_decision
                ):
                    result.correct_mappings += 1
                else:
                    result.missed_mappings += 1
                    result.messages.append(
                        f"missed:layout_{layout_index}:{column}:"
                        f"expected={expected_field}/{expected_decision},"
                        f"got={actual.canonical_field}/{actual.decision.value}"
                    )

    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.ran_successfully = False
        result.error_message = str(exc)

    return result
