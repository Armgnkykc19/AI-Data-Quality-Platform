from __future__ import annotations

from schema_mapping.config import SchemaMappingConfig


def generate_candidates(
    header: str,
    config: SchemaMappingConfig,
) -> list[str]:
    del header
    return list(config.mappable_fields)
