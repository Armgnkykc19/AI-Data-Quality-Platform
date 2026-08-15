from __future__ import annotations

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue
from validation.registry import register_rule
from validation.rules.base import is_blank, issue, severity_for


class CrossFieldCityDistrictRule:
    rule_id = "cross_field.city_district"

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]:
        city = record.get("city")
        district = record.get("district")
        if is_blank(city) or is_blank(district):
            return []

        city_value = city.strip()
        district_value = district.strip()
        allowed_districts = config.city_district_map.get(city_value)
        if allowed_districts is None:
            return []

        if district_value in allowed_districts:
            return []

        severity = severity_for(config, "cross_field")
        return [
            issue(
                field_name="district",
                rule_id=self.rule_id,
                severity=severity,
                code="city_district_mismatch",
                message=(
                    f"District {district_value!r} is not valid for city {city_value!r}"
                ),
                value=district,
            )
        ]


register_rule(CrossFieldCityDistrictRule())
