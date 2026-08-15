from __future__ import annotations

import re
import unicodedata

from normalization.config import NormalizationConfig
from normalization.models import NormalizationStatus, NormalizationTransformation
from normalization.registry import register_rule

WHITESPACE_PATTERN = re.compile(r"\s+")


def _transformation(
    *,
    field_name: str,
    rule_id: str,
    original_value: str | None,
    normalized_value: str | None,
) -> NormalizationTransformation:
    status = (
        NormalizationStatus.UNCHANGED
        if original_value == normalized_value
        else NormalizationStatus.NORMALIZED
    )
    return NormalizationTransformation(
        field_name=field_name,
        rule_id=rule_id,
        original_value=original_value,
        normalized_value=normalized_value,
        status=status,
    )


class TrimWhitespaceRule:
    rule_id = "text.trim_whitespace"
    field_name = "_all_text"

    TEXT_FIELDS = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "company",
        "city",
        "district",
        "address",
    )

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None or not config.trim_whitespace:
            return value, None
        normalized = value.strip()
        return normalized, _transformation(
            field_name="",
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class CollapseWhitespaceRule:
    rule_id = "text.collapse_whitespace"
    field_name = "_all_text"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None or not config.collapse_internal_whitespace:
            return value, None
        normalized = WHITESPACE_PATTERN.sub(" ", value.strip())
        return normalized, _transformation(
            field_name="",
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class NfcUnicodeRule:
    rule_id = "text.nfc_unicode"
    field_name = "_all_text"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        form = config.unicode_form.upper()
        if form != "NFC":
            return value, None
        normalized = unicodedata.normalize("NFC", value)
        return normalized, _transformation(
            field_name="",
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(TrimWhitespaceRule())
register_rule(CollapseWhitespaceRule())
register_rule(NfcUnicodeRule())
