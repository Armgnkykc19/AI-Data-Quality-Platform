from __future__ import annotations

from typing import Protocol

from validation.config import ValidationConfig
from validation.models import FieldValidationIssue


class ValidationRule(Protocol):
    rule_id: str

    def validate(
        self,
        record: dict[str, str | None],
        config: ValidationConfig,
    ) -> list[FieldValidationIssue]: ...


_RULES: dict[str, ValidationRule] = {}


def register_rule(rule: ValidationRule) -> ValidationRule:
    if rule.rule_id in _RULES:
        raise ValueError(f"Duplicate validation rule registration: {rule.rule_id}")
    _RULES[rule.rule_id] = rule
    return rule


def get_registered_rules() -> dict[str, ValidationRule]:
    return dict(_RULES)


def clear_rules() -> None:
    _RULES.clear()
