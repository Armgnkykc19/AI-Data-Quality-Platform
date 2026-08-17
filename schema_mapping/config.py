from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_MAPPING_CONFIG = PROJECT_ROOT / "configs" / "schema_mapping.yaml"
DEFAULT_CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "configs" / "canonical_schema.yaml"


class SchemaMappingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaMappingConfig:
    version: str
    canonical_schema_path: Path
    mappable_fields: tuple[str, ...]
    non_auto_mappable_fields: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]
    alias_to_canonical: dict[str, str]
    ambiguous_headers: frozenset[str]
    evidence_weights: dict[str, float]
    conflict_penalty: float
    auto_map_threshold: float
    review_threshold: float
    ambiguity_margin: float
    lexical_minimum: float
    pattern_dominance: float
    pattern_auto_map_support: float
    type_compatibility: dict[str, frozenset[str]]
    report_output_directory: Path
    report_json: bool
    report_markdown: bool
    raw: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def _normalize_alias_key(value: str) -> str:
    from schema_mapping.preprocessing import normalize_header

    return normalize_header(value)


def _build_alias_lookup(aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical_field, alias_list in aliases.items():
        for alias in alias_list:
            key = _normalize_alias_key(alias)
            if key in lookup and lookup[key] != canonical_field:
                raise SchemaMappingConfigError(
                    f"Duplicate conflicting alias '{alias}' maps to both "
                    f"'{lookup[key]}' and '{canonical_field}'."
                )
            lookup[key] = canonical_field
    return lookup


def load_canonical_schema(path: Path) -> dict[str, Any]:
    data = _load_yaml(path)
    fields = data.get("fields", {})
    source_fields = data.get("source_fields", {})
    if not isinstance(fields, dict):
        raise SchemaMappingConfigError("canonical_schema fields must be a mapping")
    if not isinstance(source_fields, dict):
        raise SchemaMappingConfigError("canonical_schema source_fields must be a mapping")
    return {
        "version": str(data.get("version", "0.1.0")),
        "fields": fields,
        "source_fields": source_fields,
    }


def load_schema_mapping_config(
    path: Path = DEFAULT_SCHEMA_MAPPING_CONFIG,
) -> SchemaMappingConfig:
    data = _load_yaml(path)

    aliases_raw = data.get("aliases", {})
    if not isinstance(aliases_raw, dict):
        raise SchemaMappingConfigError("schema_mapping aliases must be a mapping")

    aliases: dict[str, tuple[str, ...]] = {}
    for field_name, alias_values in aliases_raw.items():
        if not isinstance(alias_values, list):
            raise SchemaMappingConfigError(
                f"aliases.{field_name} must be a list of alias strings"
            )
        aliases[str(field_name)] = tuple(str(item) for item in alias_values)

    mappable_fields = tuple(str(item) for item in data.get("mappable_fields", []))
    non_auto_mappable = tuple(str(item) for item in data.get("non_auto_mappable_fields", []))
    if not mappable_fields:
        raise SchemaMappingConfigError("mappable_fields must not be empty")

    for field_name in mappable_fields:
        if field_name not in aliases:
            raise SchemaMappingConfigError(
                f"mappable field '{field_name}' missing alias configuration"
            )

    scoring = data.get("scoring", {})
    weights = scoring.get("weights", {})
    if not isinstance(weights, dict):
        raise SchemaMappingConfigError("scoring.weights must be a mapping")

    thresholds = data.get("thresholds", {})
    auto_map = float(thresholds.get("auto_map", 0.90))
    review = float(thresholds.get("review", 0.60))
    if review >= auto_map:
        raise SchemaMappingConfigError("review threshold must be lower than auto_map threshold")
    negative_weight_keys = {"type_incompatibility", "pattern_numeric"}
    for weight_name, weight_value in weights.items():
        if float(weight_value) < 0 and weight_name not in negative_weight_keys:
            raise SchemaMappingConfigError(
                f"negative weight not supported for '{weight_name}'"
            )

    type_compat_raw = data.get("type_compatibility", {})
    if not isinstance(type_compat_raw, dict):
        raise SchemaMappingConfigError("type_compatibility must be a mapping")
    type_compatibility = {
        str(field): frozenset(str(item) for item in values)
        for field, values in type_compat_raw.items()
    }

    canonical_schema_path = PROJECT_ROOT / str(
        data.get("canonical_schema_path", "configs/canonical_schema.yaml")
    )
    canonical_schema = load_canonical_schema(canonical_schema_path)
    known_fields = set(canonical_schema["fields"]) | set(canonical_schema["source_fields"])
    for field_name in list(mappable_fields) + list(non_auto_mappable):
        if field_name not in known_fields:
            raise SchemaMappingConfigError(
                f"configured field '{field_name}' not found in canonical schema"
            )

    reporting = data.get("reporting", {})
    ambiguous_headers = frozenset(
        _normalize_alias_key(str(item))
        for item in data.get("ambiguous_headers", [])
    )

    return SchemaMappingConfig(
        version=str(data.get("version", "0.1.0")),
        canonical_schema_path=canonical_schema_path,
        mappable_fields=mappable_fields,
        non_auto_mappable_fields=non_auto_mappable,
        aliases=aliases,
        alias_to_canonical=_build_alias_lookup(aliases),
        ambiguous_headers=ambiguous_headers,
        evidence_weights={str(k): float(v) for k, v in weights.items()},
        conflict_penalty=float(scoring.get("conflict_penalty", 0.20)),
        auto_map_threshold=auto_map,
        review_threshold=review,
        ambiguity_margin=float(thresholds.get("ambiguity_margin", 0.08)),
        lexical_minimum=float(thresholds.get("lexical_minimum", 0.55)),
        pattern_dominance=float(thresholds.get("pattern_dominance", 0.85)),
        pattern_auto_map_support=float(thresholds.get("pattern_auto_map_support", 0.90)),
        type_compatibility=type_compatibility,
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "schema_mapping/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        report_markdown=bool(reporting.get("markdown", True)),
        raw=data,
    )
