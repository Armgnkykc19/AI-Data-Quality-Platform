from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SURVIVORSHIP_CONFIG = PROJECT_ROOT / "configs" / "survivorship.yaml"


class SurvivorshipConfigError(ValueError):
    pass


@dataclass(frozen=True)
class FieldRuleConfig:
    field_name: str
    strategy: str


@dataclass(frozen=True)
class SurvivorshipConfig:
    version: str
    identity_fields: tuple[str, ...]
    forbidden_fields: frozenset[str]
    source_priority: dict[str, int]
    field_rules: dict[str, FieldRuleConfig]
    build_singleton_entities: bool
    preserve_field_conflicts: bool
    skip_clusters_with_internal_conflict: bool
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


def load_survivorship_config(
    path: Path = DEFAULT_SURVIVORSHIP_CONFIG,
) -> SurvivorshipConfig:
    data = _load_yaml(path)

    identity_fields = tuple(str(item) for item in data.get("identity_fields", []))
    if not identity_fields:
        raise SurvivorshipConfigError("identity_fields must not be empty")

    forbidden = frozenset(str(item) for item in data.get("forbidden_fields", []))
    source_priority_raw = data.get("source_priority", {})
    if not isinstance(source_priority_raw, dict) or not source_priority_raw:
        raise SurvivorshipConfigError("source_priority must be a non-empty mapping")

    source_priority = {str(k): int(v) for k, v in source_priority_raw.items()}

    field_rules_raw = data.get("field_rules", {})
    if not isinstance(field_rules_raw, dict):
        raise SurvivorshipConfigError("field_rules must be a mapping")

    field_rules: dict[str, FieldRuleConfig] = {}
    for field_name in identity_fields:
        rule = field_rules_raw.get(field_name, {"strategy": "quality_first"})
        if not isinstance(rule, dict):
            raise SurvivorshipConfigError(f"field_rules.{field_name} must be a mapping")
        field_rules[field_name] = FieldRuleConfig(
            field_name=field_name,
            strategy=str(rule.get("strategy", "quality_first")),
        )

    cluster_policy = data.get("cluster_policy", {})
    reporting = data.get("reporting", {})

    return SurvivorshipConfig(
        version=str(data.get("version", "0.1.0")),
        identity_fields=identity_fields,
        forbidden_fields=forbidden,
        source_priority=source_priority,
        field_rules=field_rules,
        build_singleton_entities=bool(cluster_policy.get("build_singleton_entities", True)),
        preserve_field_conflicts=bool(cluster_policy.get("preserve_field_conflicts", True)),
        skip_clusters_with_internal_conflict=bool(
            cluster_policy.get("skip_clusters_with_internal_conflict", False)
        ),
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "survivorship/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        report_markdown=bool(reporting.get("markdown", True)),
        raw=data,
    )
