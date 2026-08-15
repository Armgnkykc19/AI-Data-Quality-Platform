from __future__ import annotations

import re

from normalization.config import NormalizationConfig
from normalization.models import NormalizationStatus, NormalizationTransformation
from normalization.registry import register_rule

DIGITS_ONLY = re.compile(r"\D")
TR_E164_PATTERN = re.compile(r"^\+90[1-9][0-9]{9}$")


def _transformation(
    *,
    field_name: str,
    rule_id: str,
    original_value: str | None,
    normalized_value: str | None,
    status: NormalizationStatus = NormalizationStatus.NORMALIZED,
) -> NormalizationTransformation:
    if original_value == normalized_value:
        status = NormalizationStatus.UNCHANGED
    return NormalizationTransformation(
        field_name=field_name,
        rule_id=rule_id,
        original_value=original_value,
        normalized_value=normalized_value,
        status=status,
    )


def normalize_tr_phone(value: str) -> str | None:
    digits = DIGITS_ONLY.sub("", value)
    if not digits:
        return None

    if digits.startswith("90") and len(digits) == 12:
        candidate = f"+{digits}"
    elif digits.startswith("0") and len(digits) == 11:
        candidate = f"+9{digits}"
    elif len(digits) == 10 and digits[0] in "3456789":
        candidate = f"+90{digits}"
    else:
        return None

    if TR_E164_PATTERN.match(candidate):
        return candidate
    return None


class PhoneTrE164Rule:
    rule_id = "phone.tr_e164"
    field_name = "phone"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        if config.phone_region != "TR" or config.phone_target_format != "E164":
            return value, None

        stripped = value.strip()
        if TR_E164_PATTERN.match(stripped):
            return stripped, _transformation(
                field_name=self.field_name,
                rule_id=self.rule_id,
                original_value=value,
                normalized_value=stripped,
            )

        normalized = normalize_tr_phone(stripped)
        if normalized is None:
            return value, _transformation(
                field_name=self.field_name,
                rule_id=self.rule_id,
                original_value=value,
                normalized_value=value,
                status=NormalizationStatus.FAILED,
            )

        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(PhoneTrE164Rule())
