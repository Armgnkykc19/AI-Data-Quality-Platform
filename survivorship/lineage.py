from __future__ import annotations

from survivorship.models import CanonicalEntity, FieldProvenance


def build_field_lineage_index(
    entity: CanonicalEntity,
) -> dict[str, FieldProvenance]:
    return {item.field_name: item for item in entity.provenance}


def summarize_entity_lineage(entity: CanonicalEntity) -> list[dict[str, str | None]]:
    return [
        {
            "field_name": item.field_name,
            "source_record_id": item.source_record_id,
            "source_name": item.source_name,
            "source_value": item.source_value,
            "selected_value": item.selected_value,
            "rule": item.rule,
        }
        for item in entity.provenance
    ]
