from __future__ import annotations

from ingestion.models import ParsedDataset
from normalization.config import NormalizationConfig, load_normalization_config
from normalization.engine import NormalizationEngine
from normalization.pipeline import field_eligibility_from_validation
from record_quality.models import DatasetQualityResult, RecordQualityState
from validation.config import ValidationConfig, load_validation_config
from validation.engine import ValidationEngine
from validation.models import ValidationSummary
from validation.pipeline import map_row_to_canonical, validate_parsed_dataset


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
    return summary


def run_quality_pipeline(
    parsed: ParsedDataset,
    *,
    validation_config: ValidationConfig | None = None,
    normalization_config: NormalizationConfig | None = None,
) -> DatasetQualityResult:
    val_config = validation_config or load_validation_config()
    norm_config = normalization_config or load_normalization_config()

    validation_engine = ValidationEngine(val_config)
    normalization_engine = NormalizationEngine(norm_config)

    pre_results = []
    quality_records: list[RecordQualityState] = []
    total_transformations = 0
    changed_records = 0

    for row in parsed.rows:
        canonical = map_row_to_canonical(row, parsed.headers)
        pre_validation = validation_engine.validate_record(
            canonical,
            row_number=row.row_number,
        )
        pre_results.append(pre_validation)

        eligible = field_eligibility_from_validation(pre_validation)
        normalization = normalization_engine.normalize_record(
            canonical,
            row_number=row.row_number,
            field_eligibility=eligible,
        )

        post_validation = validation_engine.validate_record(
            normalization.normalized_values,
            row_number=row.row_number,
        )

        validation_issues = list(pre_validation.issues) + list(post_validation.issues)
        total_transformations += len(normalization.transformations)
        if normalization.changed_field_count > 0:
            changed_records += 1

        quality_records.append(
            RecordQualityState(
                row_number=row.row_number,
                original_values=dict(canonical),
                normalized_values=dict(normalization.normalized_values),
                pre_validation=pre_validation,
                post_validation=post_validation,
                validation_issues=validation_issues,
                transformations=list(normalization.transformations),
            )
        )

    post_results = [record.post_validation for record in quality_records if record.post_validation]

    return DatasetQualityResult(
        source_path=parsed.metadata.path,
        headers=list(parsed.headers),
        records=quality_records,
        pre_validation_summary=_build_summary(pre_results),
        post_validation_summary=_build_summary(post_results),
        total_transformations=total_transformations,
        changed_records=changed_records,
    )


def validate_only(parsed: ParsedDataset, config: ValidationConfig | None = None):
    return validate_parsed_dataset(parsed, config=config)
