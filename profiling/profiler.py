from __future__ import annotations

from ingestion.config import IngestionConfig
from ingestion.models import ParsedDataset
from profiling.models import ColumnProfile, DatasetProfile, PatternProfile, TypeInferenceResult
from profiling.patterns import profile_patterns
from profiling.type_inference import infer_type


def _is_blank(value: str | None) -> bool:
    return value is not None and value.strip() == ""


def profile_dataset(parsed: ParsedDataset, config: IngestionConfig) -> DatasetProfile:
    if parsed.accounting is None:
        parsed.finalize_accounting()
    accounting = parsed.accounting
    assert accounting is not None

    columns: list[ColumnProfile] = []
    empty_columns: list[str] = []

    for header in parsed.headers:
        values_for_row: list[str | None] = [row.values.get(header) for row in parsed.rows]
        row_count = len(values_for_row)
        null_count = sum(1 for value in values_for_row if value is None)
        blank_count = sum(1 for value in values_for_row if _is_blank(value))
        non_null_count = row_count - null_count - blank_count
        non_empty_values = [
            value.strip()
            for value in values_for_row
            if value is not None and value.strip() != ""
        ]
        unique_values = set(non_empty_values)
        completeness_ratio = non_null_count / row_count if row_count else 0.0
        uniqueness_ratio = len(unique_values) / len(non_empty_values) if non_empty_values else 0.0
        if non_empty_values == []:
            empty_columns.append(header)

        inferred_type, confidence, notes = infer_type(
            non_empty_values[: config.pattern_sample_limit]
        )
        pattern_values = non_empty_values[: config.pattern_sample_limit]
        patterns = tuple(
            PatternProfile(
                pattern_name=name,
                match_count=matches,
                sample_size=sample_size,
                match_ratio=ratio,
            )
            for name, matches, sample_size, ratio in profile_patterns(pattern_values)
        )

        columns.append(
            ColumnProfile(
                name=header,
                row_count=row_count,
                non_null_count=non_null_count,
                null_count=null_count,
                blank_count=blank_count,
                completeness_ratio=completeness_ratio,
                unique_count=len(unique_values),
                uniqueness_ratio=uniqueness_ratio,
                sample_values=tuple(non_empty_values[: config.sample_value_limit]),
                type_inference=TypeInferenceResult(
                    inferred_type=inferred_type,
                    confidence=confidence,
                    notes=notes,
                ),
                patterns=patterns,
            )
        )

    return DatasetProfile(
        format=parsed.metadata.format,
        row_count=accounting.source_data_rows,
        column_count=len(parsed.headers),
        accepted_rows=accounting.accepted_rows,
        rejected_rows=accounting.rejected_rows,
        empty_columns=tuple(empty_columns),
        parse_warning_count=sum(1 for issue in parsed.issues if issue.severity == "warning"),
        encoding=parsed.metadata.encoding,
        delimiter=parsed.metadata.delimiter,
        worksheet=parsed.metadata.worksheet,
        worksheet_selection_policy=parsed.metadata.worksheet_selection_policy,
        available_worksheets=parsed.metadata.available_worksheets,
        columns=columns,
        status="ok",
    )
