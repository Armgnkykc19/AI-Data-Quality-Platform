from __future__ import annotations

from normalization.config import NormalizationConfig
from normalization.models import NormalizationTransformation
from normalization.registry import register_rule
from normalization.rules.text import _transformation


def _resolve_alias(value: str, aliases: dict[str, str]) -> str:
    direct = aliases.get(value)
    if direct is not None:
        return direct
    lowered = aliases.get(value.lower())
    if lowered is not None:
        return lowered
    return value


class LocationCityAliasRule:
    rule_id = "location.city_alias"
    field_name = "city"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        normalized = _resolve_alias(value.strip(), config.city_aliases)
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class LocationCityCaseRule:
    rule_id = "location.city_case"
    field_name = "city"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        stripped = value.strip()
        canonical = _resolve_alias(stripped, config.city_aliases)
        if canonical != stripped:
            normalized = canonical
        elif stripped in config.city_aliases.values():
            normalized = stripped
        else:
            normalized = stripped
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class LocationDistrictAliasRule:
    rule_id = "location.district_alias"
    field_name = "district"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        normalized = _resolve_alias(value.strip(), config.district_aliases)
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class LocationDistrictCaseRule:
    rule_id = "location.district_case"
    field_name = "district"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        stripped = value.strip()
        canonical = _resolve_alias(stripped, config.district_aliases)
        if canonical != stripped:
            normalized = canonical
        elif stripped in config.district_aliases.values():
            normalized = stripped
        else:
            normalized = stripped
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(LocationCityAliasRule())
register_rule(LocationCityCaseRule())
register_rule(LocationDistrictAliasRule())
register_rule(LocationDistrictCaseRule())
