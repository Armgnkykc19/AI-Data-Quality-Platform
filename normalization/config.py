from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NORMALIZATION_CONFIG = PROJECT_ROOT / "configs" / "normalization.yaml"


@dataclass(frozen=True)
class NormalizationConfig:
    version: str
    phone_region: str
    phone_target_format: str
    enabled_rules: dict[str, bool]
    trim_whitespace: bool
    collapse_internal_whitespace: bool
    unicode_form: str
    city_aliases: dict[str, str]
    district_aliases: dict[str, str]
    company_suffix_mappings: dict[str, str]
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


def load_normalization_config(
    path: Path = DEFAULT_NORMALIZATION_CONFIG,
) -> NormalizationConfig:
    data = _load_yaml(path)

    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise ValueError("normalization config rules must be a mapping")

    phone = data.get("phone", {})
    whitespace = data.get("whitespace", {})
    location = data.get("location", {})
    company = data.get("company", {})

    city_aliases = location.get("city_aliases", {})
    district_aliases = location.get("district_aliases", {})
    suffix_mappings = company.get("suffix_mappings", {})

    if not isinstance(city_aliases, dict):
        raise ValueError("location.city_aliases must be a mapping")
    if not isinstance(district_aliases, dict):
        raise ValueError("location.district_aliases must be a mapping")
    if not isinstance(suffix_mappings, dict):
        raise ValueError("company.suffix_mappings must be a mapping")

    reporting = data.get("reporting", {})

    return NormalizationConfig(
        version=str(data.get("version", "0.1.0")),
        phone_region=str(phone.get("region", "TR")),
        phone_target_format=str(phone.get("target_format", "E164")),
        enabled_rules={str(key): bool(value) for key, value in rules.items()},
        trim_whitespace=bool(whitespace.get("trim", True)),
        collapse_internal_whitespace=bool(whitespace.get("collapse_internal", True)),
        unicode_form=str(whitespace.get("unicode_form", "NFC")),
        city_aliases={str(key): str(value) for key, value in city_aliases.items()},
        district_aliases={str(key): str(value) for key, value in district_aliases.items()},
        company_suffix_mappings={str(key): str(value) for key, value in suffix_mappings.items()},
        report_output_directory=PROJECT_ROOT
        / reporting.get("output_directory", "normalization/reports/latest"),
        report_json=bool(reporting.get("json", True)),
        raw=data,
    )
