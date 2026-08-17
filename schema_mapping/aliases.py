from __future__ import annotations

from schema_mapping.config import SchemaMappingConfig
from schema_mapping.preprocessing import normalize_header


def exact_alias_match(
    header: str,
    config: SchemaMappingConfig,
) -> str | None:
    normalized = normalize_header(header)
    return config.alias_to_canonical.get(normalized)


def alias_candidates_for_header(
    header: str,
    config: SchemaMappingConfig,
) -> list[str]:
    canonical = exact_alias_match(header, config)
    if canonical is None:
        return []
    return [canonical]
