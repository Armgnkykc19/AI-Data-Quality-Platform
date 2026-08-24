from __future__ import annotations

from ingestion.config import load_ingestion_config
from ingestion.models import ParsedDataset
from normalization.config import NormalizationConfig, load_normalization_config
from normalization.engine import ALL_NORMALIZABLE_FIELDS, NormalizationEngine
from normalization.models import DatasetNormalizationResult
from profiling.profiler import profile_dataset
from schema_mapping.apply import apply_mapping_plan
from schema_mapping.engine import build_mapping_plan
from validation.eligibility import BLOCKING_ELIGIBILITIES
from validation.models import NormalizationEligibility, RecordValidationResult, Severity


def field_eligibility_from_validation(
    result: RecordValidationResult,
) -> dict[str, NormalizationEligibility]:
    eligibility = {
        field_name: NormalizationEligibility.SAFE for field_name in ALL_NORMALIZABLE_FIELDS
    }
    for issue in result.issues:
        if issue.field_name not in eligibility:
            continue
        if issue.severity != Severity.ERROR:
            continue
        if issue.rule_id == "required.missing":
            eligibility[issue.field_name] = NormalizationEligibility.NOT_APPLICABLE
        elif issue.normalization_eligibility in BLOCKING_ELIGIBILITIES:
            eligibility[issue.field_name] = issue.normalization_eligibility
    return eligibility


def field_has_blocking_validation_issues(
    result: RecordValidationResult | None,
    field_name: str,
) -> bool:
    if result is None:
        return False
    for issue in result.issues:
        if issue.field_name != field_name or issue.severity != Severity.ERROR:
            continue
        if issue.rule_id == "required.missing":
            return True
        if issue.normalization_eligibility in BLOCKING_ELIGIBILITIES:
            return True
    return False


def normalize_parsed_dataset(
    parsed: ParsedDataset,
    *,
    config: NormalizationConfig | None = None,
    validation_results: list[RecordValidationResult] | None = None,
) -> DatasetNormalizationResult:
    normalization_config = config or load_normalization_config()
    engine = NormalizationEngine(normalization_config)

    validation_by_row = {item.row_number: item for item in (validation_results or [])}

    ingestion_config = load_ingestion_config()
    profile = profile_dataset(parsed, ingestion_config)
    mapping_plan = build_mapping_plan(parsed, profile=profile)
    applied = apply_mapping_plan(parsed, mapping_plan)
    canonical_by_row = {record.row_number: record.canonical_values for record in applied.records}

    records = []
    changed_records = 0
    total_transformations = 0

    for row in parsed.rows:
        canonical = canonical_by_row[row.row_number]
        validation_result = validation_by_row.get(row.row_number)
        field_eligibility = (
            field_eligibility_from_validation(validation_result)
            if validation_result is not None
            else None
        )
        record_result = engine.normalize_record(
            canonical,
            row_number=row.row_number,
            field_eligibility=field_eligibility,
        )
        records.append(record_result)
        if record_result.changed_field_count > 0:
            changed_records += 1
        total_transformations += len(record_result.transformations)

    return DatasetNormalizationResult(
        source_path=parsed.metadata.path,
        records=records,
        total_records=len(records),
        changed_records=changed_records,
        total_transformations=total_transformations,
    )
