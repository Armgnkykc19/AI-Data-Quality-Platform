from __future__ import annotations

from normalization.config import NormalizationConfig
from normalization.models import NormalizationTransformation
from normalization.registry import register_rule
from normalization.rules.text import _transformation


class EmailTrimRule:
    rule_id = "email.trim"
    field_name = "email"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        normalized = value.strip()
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


class EmailLowerDomainRule:
    rule_id = "email.lower_domain"
    field_name = "email"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None or "@" not in value:
            return value, None
        local, domain = value.rsplit("@", 1)
        normalized = f"{local}@{domain.lower()}"
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(EmailTrimRule())
register_rule(EmailLowerDomainRule())
