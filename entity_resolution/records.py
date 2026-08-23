from __future__ import annotations

from entity_resolution.models import EntityRecord
from ingestion.models import ParsedDataset, ParsedRow
from record_quality.models import DatasetQualityResult

SOURCE_RECORD_ID_HEADERS = (
    "source_record_id",
    "source_record",
    "record_id",
)
SOURCE_NAME_HEADERS = (
    "source_name",
    "source",
)


def _extract_metadata_value(
    row: ParsedRow,
    headers: tuple[str, ...],
) -> str | None:
    for header in headers:
        if header in row.values:
            value = row.values.get(header)
            if value not in (None, ""):
                return str(value)
    return None


def build_entity_record(
    *,
    row: ParsedRow,
    normalized_values: dict[str, str | None],
    default_source_name: str,
    row_fallback_id: str,
) -> EntityRecord:
    record_id = _extract_metadata_value(row, SOURCE_RECORD_ID_HEADERS) or row_fallback_id
    source_name = _extract_metadata_value(row, SOURCE_NAME_HEADERS) or default_source_name

    field_values = {
        field_name: normalized_values.get(field_name)
        for field_name in normalized_values
        if field_name not in {"person_id", "source_record_id", "source_name"}
    }

    return EntityRecord(
        record_id=record_id,
        source_name=source_name,
        field_values=field_values,
    )


def build_entity_records_from_quality_result(
    parsed: ParsedDataset,
    quality_result: DatasetQualityResult,
) -> list[EntityRecord]:
    default_source_name = parsed.metadata.path.split("/")[-1].replace(".csv", "")
    row_by_number = {row.row_number: row for row in parsed.rows}
    records: list[EntityRecord] = []

    for quality_record in quality_result.records:
        row = row_by_number[quality_record.row_number]
        records.append(
            build_entity_record(
                row=row,
                normalized_values=dict(quality_record.normalized_values),
                default_source_name=default_source_name,
                row_fallback_id=f"row-{quality_record.row_number}",
            )
        )

    return records
