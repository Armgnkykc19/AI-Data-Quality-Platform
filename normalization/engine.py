from __future__ import annotations

import normalization.rules  # noqa: F401  — register built-in rules
from normalization.config import NormalizationConfig
from normalization.models import (
    NormalizationStatus,
    NormalizationTransformation,
    RecordNormalizationResult,
)
from normalization.registry import get_registered_rules
from validation.models import NormalizationEligibility

TEXT_RULE_IDS = (
    "text.trim_whitespace",
    "text.collapse_whitespace",
    "text.nfc_unicode",
)

FIELD_RULE_ORDER: dict[str, tuple[str, ...]] = {
    "first_name": TEXT_RULE_IDS,
    "last_name": TEXT_RULE_IDS,
    "email": TEXT_RULE_IDS + ("email.trim", "email.lower_domain"),
    "phone": TEXT_RULE_IDS + ("phone.tr_e164",),
    "company": TEXT_RULE_IDS + ("company.canonical_suffix",),
    "city": TEXT_RULE_IDS + ("location.city_alias", "location.city_case"),
    "district": TEXT_RULE_IDS + ("location.district_alias", "location.district_case"),
    "address": TEXT_RULE_IDS + ("address.whitespace_cleanup",),
}

ALL_NORMALIZABLE_FIELDS = tuple(FIELD_RULE_ORDER.keys())


class NormalizationEngine:
    def __init__(self, config: NormalizationConfig) -> None:
        self._config = config
        self._rules = get_registered_rules()

    def _find_rule(self, field_name: str, rule_id: str):
        direct_key = f"{field_name}:{rule_id}"
        if direct_key in self._rules:
            return self._rules[direct_key]
        generic_key = f"_all_text:{rule_id}"
        return self._rules.get(generic_key)

    def _apply_field_rules(
        self,
        field_name: str,
        value: str | None,
        *,
        original_value: str | None,
    ) -> tuple[str | None, list[NormalizationTransformation]]:
        if field_name not in FIELD_RULE_ORDER:
            return value, []

        current = value
        transformations: list[NormalizationTransformation] = []

        for rule_id in FIELD_RULE_ORDER[field_name]:
            if not self._config.enabled_rules.get(rule_id, True):
                continue
            rule = self._find_rule(field_name, rule_id)
            if rule is None:
                continue

            next_value, transformation = rule.normalize(current, self._config)
            if (
                transformation is not None
                and transformation.status == NormalizationStatus.NORMALIZED
            ):
                audit = NormalizationTransformation(
                    field_name=field_name,
                    rule_id=transformation.rule_id,
                    original_value=original_value,
                    normalized_value=next_value,
                    status=transformation.status,
                )
                current = next_value
                transformations.append(audit)
            elif transformation is not None and transformation.status != NormalizationStatus.FAILED:
                current = next_value

        return current, transformations

    def normalize_field(
        self,
        field_name: str,
        value: str | None,
        *,
        original_value: str | None,
        normalization_eligibility: NormalizationEligibility,
    ) -> tuple[str | None, list[NormalizationTransformation]]:
        if normalization_eligibility is not NormalizationEligibility.SAFE:
            return value, []
        return self._apply_field_rules(
            field_name,
            value,
            original_value=original_value,
        )

    def normalize_record(
        self,
        record: dict[str, str | None],
        *,
        row_number: int = 0,
        field_eligibility: dict[str, NormalizationEligibility] | None = None,
    ) -> RecordNormalizationResult:
        original_values = dict(record)
        normalized_values = dict(record)
        all_transformations: list[NormalizationTransformation] = []
        changed_field_count = 0
        eligibility_by_field = field_eligibility or {}

        for field_name in ALL_NORMALIZABLE_FIELDS:
            original = record.get(field_name)
            field_eligibility_value = eligibility_by_field.get(
                field_name,
                NormalizationEligibility.NOT_APPLICABLE,
            )
            normalized, transformations = self.normalize_field(
                field_name,
                original,
                original_value=original,
                normalization_eligibility=field_eligibility_value,
            )
            normalized_values[field_name] = normalized
            all_transformations.extend(transformations)
            if normalized != original:
                changed_field_count += 1

        return RecordNormalizationResult(
            row_number=row_number,
            original_values=original_values,
            normalized_values=normalized_values,
            transformations=tuple(all_transformations),
            changed_field_count=changed_field_count,
        )
