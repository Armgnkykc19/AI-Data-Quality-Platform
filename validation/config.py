from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_CONFIG = PROJECT_ROOT / "configs" / "validation.yaml"


@dataclass(frozen=True)
class ValidationConfig:
    version: str
    required_fields: tuple[str, ...]
    enabled_rules: dict[str, bool]
    default_severities: dict[str, str]
    text_max_lengths: dict[str, int]
    known_cities: tuple[str, ...]
    known_districts: tuple[str, ...]
    city_district_map: dict[str, tuple[str, ...]]
    report_output_directory: Path
    report_json: bool
    raw: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def load_validation_config(path: Path = DEFAULT_VALIDATION_CONFIG) -> ValidationConfig:
    data = _load_yaml(path)

    required = data.get("required_fields")
    if not isinstance(required, list) or not required:
        raise ValueError("validation config requires non-empty required_fields")

    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise ValueError("validation config rules must be a mapping")

    defaults = data.get("defaults", {})
    severities = defaults.get("severity", {})
    if not isinstance(severities, dict):
        raise ValueError("defaults.severity must be a mapping")

    text_max_lengths = data.get("text_max_lengths", {})
    if not isinstance(text_max_lengths, dict):
        raise ValueError("text_max_lengths must be a mapping")

    location = data.get("location", {})
    known_cities = location.get("known_cities", [])
    known_districts = location.get("known_districts", [])
    if not known_cities or not known_districts:
        raise ValueError("location.known_cities and location.known_districts are required")

    cross_field = data.get("cross_field", {})
    raw_map = cross_field.get("city_district_map", {})
    if not isinstance(raw_map, dict):
        raise ValueError("cross_field.city_district_map must be a mapping")

    city_district_map: dict[str, tuple[str, ...]] = {}
    for city, districts in raw_map.items():
        if not isinstance(districts, list) or not districts:
            raise ValueError(f"city_district_map entry for {city!r} must be a non-empty list")
        city_district_map[str(city)] = tuple(str(item) for item in districts)

    reporting = data.get("reporting", {})

    return ValidationConfig(
        version=str(data.get("version", "0.1.0")),
        required_fields=tuple(str(item) for item in required),
        enabled_rules={str(key): bool(value) for key, value in rules.items()},
        default_severities={str(key): str(value) for key, value in severities.items()},
        text_max_lengths={str(key): int(value) for key, value in text_max_lengths.items()},
        known_cities=tuple(str(item) for item in known_cities),
        known_districts=tuple(str(item) for item in known_districts),
        city_district_map=city_district_map,
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "validation/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        raw=data,
    )
