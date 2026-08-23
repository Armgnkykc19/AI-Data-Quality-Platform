from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENTITY_RESOLUTION_CONFIG = PROJECT_ROOT / "configs" / "entity_resolution.yaml"


class EntityResolutionConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BlockingStrategyConfig:
    strategy_id: str
    reason: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class EntityResolutionConfig:
    version: str
    identity_fields: tuple[str, ...]
    forbidden_match_fields: frozenset[str]
    blocking_strategies: tuple[BlockingStrategyConfig, ...]
    blocking_min_key_length: int
    evidence_weights: dict[str, float]
    conflict_penalties: dict[str, float]
    fuzzy_minimum: float
    company_conflict_threshold: float
    auto_match_threshold: float
    review_threshold: float
    ambiguity_margin: float
    require_strong_identity: bool
    strong_evidence_types: frozenset[str]
    forbid_severe_conflicts: bool
    severe_conflict_types: frozenset[str]
    weak_only_forces_review: bool
    weak_evidence_types: frozenset[str]
    clustering_enabled: bool
    cluster_conflict_guard: bool
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


def load_entity_resolution_config(
    path: Path = DEFAULT_ENTITY_RESOLUTION_CONFIG,
) -> EntityResolutionConfig:
    data = _load_yaml(path)

    identity_fields = tuple(str(item) for item in data.get("identity_fields", []))
    if not identity_fields:
        raise EntityResolutionConfigError("identity_fields must not be empty")

    forbidden = frozenset(str(item) for item in data.get("forbidden_match_fields", []))
    for field_name in forbidden:
        if field_name in identity_fields:
            raise EntityResolutionConfigError(
                f"forbidden_match_fields contains identity field '{field_name}'"
            )

    blocking = data.get("blocking", {})
    strategies_raw = blocking.get("strategies", [])
    if not isinstance(strategies_raw, list) or not strategies_raw:
        raise EntityResolutionConfigError("blocking.strategies must be a non-empty list")

    strategies: list[BlockingStrategyConfig] = []
    for item in strategies_raw:
        if not isinstance(item, dict):
            raise EntityResolutionConfigError("each blocking strategy must be a mapping")
        fields = tuple(str(field) for field in item.get("fields", []))
        if not fields:
            raise EntityResolutionConfigError("blocking strategy fields must not be empty")
        for field_name in fields:
            if field_name not in identity_fields:
                raise EntityResolutionConfigError(
                    f"blocking strategy references unknown field '{field_name}'"
                )
        strategies.append(
            BlockingStrategyConfig(
                strategy_id=str(item.get("id", "")),
                reason=str(item.get("reason", "")),
                fields=fields,
            )
        )

    scoring = data.get("scoring", {})
    weights = scoring.get("weights", {})
    penalties = scoring.get("conflict_penalties", {})
    if not isinstance(weights, dict) or not weights:
        raise EntityResolutionConfigError("scoring.weights must be a non-empty mapping")
    if not isinstance(penalties, dict):
        raise EntityResolutionConfigError("scoring.conflict_penalties must be a mapping")

    thresholds = data.get("thresholds", {})
    auto_match = float(thresholds.get("auto_match", 0.88))
    review = float(thresholds.get("review", 0.50))
    if review >= auto_match:
        raise EntityResolutionConfigError("review threshold must be lower than auto_match")

    auto_rules = data.get("auto_match_rules", {})
    clustering = data.get("clustering", {})
    reporting = data.get("reporting", {})
    similarity = data.get("similarity", {})

    return EntityResolutionConfig(
        version=str(data.get("version", "0.1.0")),
        identity_fields=identity_fields,
        forbidden_match_fields=forbidden,
        blocking_strategies=tuple(strategies),
        blocking_min_key_length=int(blocking.get("min_key_length", 2)),
        evidence_weights={str(k): float(v) for k, v in weights.items()},
        conflict_penalties={str(k): float(v) for k, v in penalties.items()},
        fuzzy_minimum=float(similarity.get("fuzzy_minimum", 0.82)),
        company_conflict_threshold=float(similarity.get("company_conflict_threshold", 0.60)),
        auto_match_threshold=auto_match,
        review_threshold=review,
        ambiguity_margin=float(thresholds.get("ambiguity_margin", 0.05)),
        require_strong_identity=bool(auto_rules.get("require_strong_identity", True)),
        strong_evidence_types=frozenset(
            str(item) for item in auto_rules.get("strong_evidence_types", [])
        ),
        forbid_severe_conflicts=bool(auto_rules.get("forbid_severe_conflicts", True)),
        severe_conflict_types=frozenset(
            str(item) for item in auto_rules.get("severe_conflict_types", [])
        ),
        weak_only_forces_review=bool(auto_rules.get("weak_only_forces_review", True)),
        weak_evidence_types=frozenset(
            str(item) for item in auto_rules.get("weak_evidence_types", [])
        ),
        clustering_enabled=bool(clustering.get("enabled", True)),
        cluster_conflict_guard=bool(clustering.get("conflict_guard", True)),
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "entity_resolution/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        report_markdown=bool(reporting.get("markdown", True)),
        raw=data,
    )
