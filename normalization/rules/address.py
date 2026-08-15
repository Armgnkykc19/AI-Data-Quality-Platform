from __future__ import annotations

import re

from normalization.config import NormalizationConfig
from normalization.models import NormalizationTransformation
from normalization.registry import register_rule
from normalization.rules.text import _transformation

WHITESPACE_PATTERN = re.compile(r"\s+")


class AddressWhitespaceCleanupRule:
    rule_id = "address.whitespace_cleanup"
    field_name = "address"

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]:
        if value is None:
            return value, None
        stripped = value.strip()
        normalized = WHITESPACE_PATTERN.sub(" ", stripped)
        return normalized, _transformation(
            field_name=self.field_name,
            rule_id=self.rule_id,
            original_value=value,
            normalized_value=normalized,
        )


register_rule(AddressWhitespaceCleanupRule())
