from __future__ import annotations

from typing import Protocol

from normalization.config import NormalizationConfig
from normalization.models import NormalizationTransformation


class NormalizationRule(Protocol):
    rule_id: str
    field_name: str

    def normalize(
        self,
        value: str | None,
        config: NormalizationConfig,
    ) -> tuple[str | None, NormalizationTransformation | None]: ...


_RULES: dict[str, NormalizationRule] = {}


def register_rule(rule: NormalizationRule) -> NormalizationRule:
    key = f"{rule.field_name}:{rule.rule_id}"
    if key in _RULES:
        raise ValueError(f"Duplicate normalization rule registration: {key}")
    _RULES[key] = rule
    return rule


def get_registered_rules() -> dict[str, NormalizationRule]:
    return dict(_RULES)


def clear_rules() -> None:
    _RULES.clear()
