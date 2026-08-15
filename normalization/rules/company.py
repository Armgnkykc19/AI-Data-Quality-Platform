from __future__ import annotations

from normalization.config import NormalizationConfig
from normalization.models import NormalizationTransformation
from normalization.registry import register_rule
from normalization.rules.text import _transformation


class CompanyCanonicalSuffixRule:
    rule_id = "company.canonical_suffix"
    field_name = "company"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None

        normalized = value
        for source, target in config.company_suffix_mappings.items():
            if normalized.endswith(source):
                normalized = normalized[: -len(source)] + target
                break
            if f" {source}" in normalized:
                normalized = normalized.replace(f" {source}", f" {target}")
                break

        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(CompanyCanonicalSuffixRule())
