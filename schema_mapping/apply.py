from __future__ import annotations

from ingestion.models import ParsedDataset
from schema_mapping.config import SchemaMappingConfig, load_schema_mapping_config
from schema_mapping.models import (
    CanonicalMappedRecord,
    FieldLineage,
    MappingApplicationResult,
    MappingDecisionType,
    MappingPlan,
)


def apply_mapping_plan(
    parsed: ParsedDataset,
    plan: MappingPlan,
    *,
    config: SchemaMappingConfig | None = None,
) -> MappingApplicationResult:
    mapping_config = config or load_schema_mapping_config()

    auto_map_by_source: dict[str, str] = {}
    review_fields: list[str] = []
    unmapped_columns: list[str] = []

    for mapping in plan.column_mappings:
        if mapping.decision == MappingDecisionType.AUTO_MAP and mapping.canonical_field:
            auto_map_by_source[mapping.source_column] = mapping.canonical_field
        elif mapping.decision == MappingDecisionType.REVIEW and mapping.canonical_field:
            review_fields.append(mapping.source_column)
        else:
            unmapped_columns.append(mapping.source_column)

    records: list[CanonicalMappedRecord] = []
    for row in parsed.rows:
        canonical_values: dict[str, str | None] = {
            field: None for field in mapping_config.mappable_fields
        }
        unmapped_values: dict[str, str | None] = {}
        lineage: list[FieldLineage] = []

        for header in parsed.headers:
            value = row.values.get(header)
            canonical_field = auto_map_by_source.get(header)
            if canonical_field is not None:
                if canonical_field in canonical_values and value not in (None, ""):
                    canonical_values[canonical_field] = value
                elif canonical_field not in canonical_values or canonical_values.get(
                    canonical_field
                ) in (None, ""):
                    canonical_values[canonical_field] = value
                lineage.append(
                    FieldLineage(
                        source_column=header,
                        canonical_field=canonical_field,
                        source_value=value,
                        mapped_value=canonical_values.get(canonical_field),
                    )
                )
            else:
                unmapped_values[header] = value

        records.append(
            CanonicalMappedRecord(
                row_number=row.row_number,
                canonical_values=canonical_values,
                unmapped_source_values=unmapped_values,
                lineage=tuple(lineage),
            )
        )

    return MappingApplicationResult(
        source_path=parsed.metadata.path,
        records=records,
        auto_map_fields_applied=tuple(sorted(set(auto_map_by_source.values()))),
        review_fields_skipped=tuple(sorted(review_fields)),
        unmapped_source_columns=tuple(sorted(unmapped_columns)),
        missing_canonical_fields=plan.summary.missing_canonical_fields,
    )
