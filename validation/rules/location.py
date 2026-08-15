from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for


class LocationCityKnownRule:
    rule_id = "location.city_known"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("city")
        if is_blank(value):
            return []

        severity = severity_for(config, "location")
        stripped = value.strip()
        if stripped in config.known_cities:
            return []

        return [
            issue(
                field_name="city",
                rule_id=self.rule_id,
                severity=severity,
                code="unknown_city",
                message="City is not in the known city list",
                value=value,
            )
        ]


class LocationDistrictKnownRule:
    rule_id = "location.district_known"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        value = record.get("district")
        if is_blank(value):
            return []

        severity = severity_for(config, "location")
        stripped = value.strip()
        if stripped in config.known_districts:
            return []

        return [
            issue(
                field_name="district",
                rule_id=self.rule_id,
                severity=severity,
                code="unknown_district",
                message="District is not in the known district list",
                value=value,
            )
        ]


register_rule(LocationCityKnownRule())
register_rule(LocationDistrictKnownRule())
