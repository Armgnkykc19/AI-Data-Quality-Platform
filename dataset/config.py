from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_CONFIG = PROJECT_ROOT / "configs" / "dataset.yaml"
DEFAULT_CORRUPTIONS_CONFIG = PROJECT_ROOT / "configs" / "corruptions.yaml"
DEFAULT_SCHEMA_CONFIG = PROJECT_ROOT / "configs" / "canonical_schema.yaml"

CANONICAL_FIELDS = (
    "person_id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "city",
    "district",
    "address",
)


@dataclass(frozen=True)
class DatasetConfig:
    version: str
    seed: int
    record_count: int
    min_record_count: int
    max_record_count: int
    output_base: Path
    schema_path: Path
    splits: dict[str, Any]
    sources: dict[str, Any]
    hard_cases: dict[str, Any]
    malformed: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class CorruptionsConfig:
    version: str
    seed: int
    profiles: dict[str, Any]
    severities: dict[str, str]
    raw: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")

    return data


def load_schema_config(path: Path = DEFAULT_SCHEMA_CONFIG) -> dict[str, Any]:
    data = _load_yaml(path)
    if "fields" not in data:
        raise ValueError(f"Schema config missing 'fields': {path}")
    return data


def load_dataset_config(path: Path = DEFAULT_DATASET_CONFIG) -> DatasetConfig:
    data = _load_yaml(path)

    generation = data.get("generation", {})
    output = data.get("output", {})
    schema = data.get("schema", {})

    record_count = int(generation.get("record_count", 10000))
    min_count = int(generation.get("min_record_count", 5000))
    max_count = int(generation.get("max_record_count", 20000))

    if not min_count <= record_count <= max_count:
        raise ValueError(f"record_count {record_count} must be between {min_count} and {max_count}")

    schema_path = PROJECT_ROOT / schema.get("config_path", "configs/canonical_schema.yaml")
    output_base = PROJECT_ROOT / output.get("base_directory", "datasets/golden/v0.1.0")

    return DatasetConfig(
        version=str(data.get("version", "0.1.0")),
        seed=int(data.get("seed", 42)),
        record_count=record_count,
        min_record_count=min_count,
        max_record_count=max_count,
        output_base=output_base,
        schema_path=schema_path,
        splits=dict(data.get("splits", {})),
        sources=dict(data.get("sources", {})),
        hard_cases=dict(data.get("hard_cases", {})),
        malformed=dict(data.get("malformed", {})),
        raw=data,
    )


def load_corruptions_config(path: Path = DEFAULT_CORRUPTIONS_CONFIG) -> CorruptionsConfig:
    data = _load_yaml(path)

    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Corruptions config missing 'profiles': {path}")

    return CorruptionsConfig(
        version=str(data.get("version", "0.1.0")),
        seed=int(data.get("seed", 42)),
        profiles=profiles,
        severities=dict(data.get("severities", {})),
        raw=data,
    )


def validate_canonical_record(record: dict[str, Any]) -> None:
    missing = [field for field in CANONICAL_FIELDS if field not in record]
    if missing:
        raise ValueError(f"Canonical record missing required fields: {missing}")

    person_id = record["person_id"]
    if not isinstance(person_id, str) or not person_id.startswith("P-"):
        raise ValueError(f"Invalid person_id format: {person_id!r}")
