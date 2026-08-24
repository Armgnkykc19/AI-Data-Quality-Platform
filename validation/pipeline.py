from __future__ import annotations

from ingestion.config import load_ingestion_config
from ingestion.models import ParsedDataset, ParsedRow
from profiling.profiler import profile_dataset
from schema_mapping.apply import apply_mapping_plan
from schema_mapping.engine import build_mapping_plan
from schema_mapping.models import MappingPlan
from validation.config import ValidationConfig, load_validation_config
from validation.engine import ValidationEngine
from validation.models import DatasetValidationResult, ValidationSummary


def map_row_to_canonical(
    row: ParsedRow,
    headers: list[str],
    *,
    mapping_plan: MappingPlan | None = None,
    parsed: ParsedDataset | None = None,
) -> dict[str, str | None]:
    """Map a parsed row to canonical field names using an approved mapping plan."""
    if mapping_plan is not None and parsed is not None:
        applied = apply_mapping_plan(parsed, mapping_plan)
        for record in applied.records:
            if record.row_number == row.row_number:
                return dict(record.canonical_values)
        return {}

    if parsed is not None and mapping_plan is None:
        ingestion_config = load_ingestion_config()
        profile = profile_dataset(parsed, ingestion_config)
        plan = build_mapping_plan(parsed, profile=profile)
        applied = apply_mapping_plan(parsed, plan)
        for record in applied.records:
            if record.row_number == row.row_number:
                return dict(record.canonical_values)

    from schema_mapping.config import load_schema_mapping_config
    from schema_mapping.preprocessing import normalize_header

    config = load_schema_mapping_config()
    canonical: dict[str, str | None] = {field: None for field in config.mappable_fields}
    for header in headers:
        normalized = normalize_header(header)
        target = config.alias_to_canonical.get(normalized)
        if target is None:
            continue
        value = row.values.get(header)
        if target in canonical and value not in (None, ""):
            canonical[target] = value
        elif target not in canonical or canonical.get(target) in (None, ""):
            canonical[target] = value
    return canonical


def _build_summary(records: list) -> ValidationSummary:
    summary = ValidationSummary(total_records=len(records))
    for record in records:
        if record.is_valid:
            summary.valid_records += 1
        else:
            summary.invalid_records += 1
        summary.total_issues += len(record.issues)
        summary.error_count += record.error_count
        summary.warning_count += record.warning_count
        summary.info_count += record.info_count
        for issue in record.issues:
            summary.issues_by_rule[issue.rule_id] = summary.issues_by_rule.get(issue.rule_id, 0) + 1
            summary.issues_by_field[issue.field_name] = (
                summary.issues_by_field.get(issue.field_name, 0) + 1
            )
    return summary


def validate_parsed_dataset(
    parsed: ParsedDataset,
    config: ValidationConfig | None = None,
) -> DatasetValidationResult:
    validation_config = config or load_validation_config()
    engine = ValidationEngine(validation_config)

    ingestion_config = load_ingestion_config()
    profile = profile_dataset(parsed, ingestion_config)
    mapping_plan = build_mapping_plan(parsed, profile=profile)
    applied = apply_mapping_plan(parsed, mapping_plan)

    record_results = [
        engine.validate_record(
            record.canonical_values,
            row_number=record.row_number,
        )
        for record in applied.records
    ]

    return DatasetValidationResult(
        source_path=parsed.metadata.path,
        headers=list(parsed.headers),
        records=record_results,
        summary=_build_summary(record_results),
    )
