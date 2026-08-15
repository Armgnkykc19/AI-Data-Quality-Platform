from __future__ import annotations

from validation.models import NormalizationEligibility

DEFAULT_RULE_ELIGIBILITY: dict[str, NormalizationEligibility] = {
    "required.missing": NormalizationEligibility.NOT_APPLICABLE,
    "text.blank": NormalizationEligibility.SAFE,
    "text.max_length": NormalizationEligibility.UNSUPPORTED,
    "text.noncanonical_whitespace": NormalizationEligibility.SAFE,
    "email.syntax": NormalizationEligibility.AMBIGUOUS,
    "phone.format": NormalizationEligibility.AMBIGUOUS,
    "phone.tr_e164": NormalizationEligibility.SAFE,
    "company.min_length": NormalizationEligibility.UNSUPPORTED,
    "address.min_length": NormalizationEligibility.UNSUPPORTED,
    "location.city_known": NormalizationEligibility.UNSUPPORTED,
    "location.district_known": NormalizationEligibility.UNSUPPORTED,
    "cross_field.city_district": NormalizationEligibility.AMBIGUOUS,
    "type.string": NormalizationEligibility.UNSUPPORTED,
}

BLOCKING_ELIGIBILITIES = frozenset(
    {
        NormalizationEligibility.AMBIGUOUS,
        NormalizationEligibility.UNSUPPORTED,
    }
)


def eligibility_for_rule(rule_id: str) -> NormalizationEligibility:
    return DEFAULT_RULE_ELIGIBILITY.get(
        rule_id,
        NormalizationEligibility.UNSUPPORTED,
    )
