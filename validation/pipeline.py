from __future__ import annotations

from ingestion.models import ParsedDataset, ParsedRow
from validation.config import ValidationConfig, load_validation_config
from validation.engine import ValidationEngine
from validation.models import DatasetValidationResult, ValidationSummary

HEADER_ALIASES: dict[str, str] = {
    "ad": "first_name",
    "given_name": "first_name",
    "first_name": "first_name",
    "soyad": "last_name",
    "surname": "last_name",
    "last_name": "last_name",
    "e_mail": "email",
    "email_address": "email",
    "email": "email",
    "gsm": "phone",
    "mobile": "phone",
    "cep_telefonu": "phone",
    "phone": "phone",
    "sirket": "company",
    "organization": "company",
    "company": "company",
    "sehir": "city",
    "il": "city",
    "city": "city",
    "ilce": "district",
    "mahalle": "district",
    "district": "district",
    "adres": "address",
    "street": "address",
    "address": "address",
    "person_id": "person_id",
}


def map_header_to_canonical(header: str) -> str | None:
    normalized = header.strip().lower()
    return HEADER_ALIASES.get(normalized)


def map_row_to_canonical(row: ParsedRow, headers: list[str]) -> dict[str, str | None]:
    canonical: dict[str, str | None] = {}
    for header in headers:
        canonical_field = map_header_to_canonical(header)
        if canonical_field is None:
            continue
        value = row.values.get(header)
        if canonical_field in canonical and value not in (None, ""):
            canonical[canonical_field] = value
        elif canonical_field not in canonical:
            canonical[canonical_field] = value
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
            summary.issues_by_rule[issue.rule_id] = (
                summary.issues_by_rule.get(issue.rule_id, 0) + 1
            )
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

    record_results = [
        engine.validate_record(
            map_row_to_canonical(row, parsed.headers),
            row_number=row.row_number,
        )
        for row in parsed.rows
    ]

    return DatasetValidationResult(
        source_path=parsed.metadata.path,
        headers=list(parsed.headers),
        records=record_results,
        summary=_build_summary(record_results),
    )
