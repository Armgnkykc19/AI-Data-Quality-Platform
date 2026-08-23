from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from entity_resolution.models import EntityRecord
from entity_resolution.similarity import normalize_email, normalize_phone, normalize_text
from survivorship.config import SurvivorshipConfig
from validation.config import load_validation_config
from validation.engine import ValidationEngine
from validation.models import FieldValidationIssue, NormalizationEligibility, Severity
from validation.rules.phone import TR_E164_PATTERN

ELIGIBILITY_RANK = {
    NormalizationEligibility.SAFE: 0,
    NormalizationEligibility.NOT_APPLICABLE: 1,
    NormalizationEligibility.AMBIGUOUS: 2,
    NormalizationEligibility.UNSUPPORTED: 3,
}

CORRUPTION_MARKER_PATTERN = re.compile(r"\((?:Merged|Duplicate)\)", re.IGNORECASE)
COMPANY_SUFFIX_COMMA_CORRUPTION = re.compile(r"A\s*,\s*Ş\s*,", re.IGNORECASE)
PERSON_FIELD_LEGAL_SUFFIX = re.compile(
    r"\b(?:Ltd\.?\s*Şti\.?|Ltd\.?|A\.?\s*Ş\.?|A,Ş,)\s*$",
    re.IGNORECASE,
)
ADDRESS_CAD_COMMA = re.compile(r"\bCad\s*,", re.IGNORECASE)
ADDRESS_TRAILING_LEGAL_SUFFIX = re.compile(r"\s+Ltd\.?\s*$", re.IGNORECASE)
IDENTITY_FIELDS = frozenset({"email", "phone"})


@dataclass(frozen=True)
class FieldCandidate:
    record: EntityRecord
    field_name: str
    raw_value: str | None
    normalized_value: str | None


@dataclass(frozen=True)
class FieldCandidateQuality:
    candidate: FieldCandidate
    is_present: bool
    error_count: int
    warning_count: int
    structural_penalty: int
    eligibility_rank: int
    identity_bonus: int
    information_length: int
    source_diversity_support: int
    source_priority: int
    record_id: str
    selection_reason: str

    @property
    def ranking_tuple(self) -> tuple[int | str, ...]:
        return (
            0 if self.is_present else 1,
            self.error_count,
            self.warning_count,
            self.structural_penalty,
            self.eligibility_rank,
            -self.identity_bonus,
            -self.source_diversity_support,
            -self.information_length,
            self.source_priority,
            self.record_id,
        )


@lru_cache(maxsize=1)
def _validation_engine() -> ValidationEngine:
    return ValidationEngine(load_validation_config())


def _normalize_field(field_name: str, value: str | None) -> str | None:
    if field_name == "email":
        return normalize_email(value)
    if field_name == "phone":
        return normalize_phone(value)
    return normalize_text(value)


def _context_record(
    record: EntityRecord,
    field_name: str,
    raw_value: str | None,
) -> dict[str, str | None]:
    context = dict(record.field_values)
    context[field_name] = raw_value
    return context


def _issues_for_field(
    issues: tuple[FieldValidationIssue, ...],
    field_name: str,
) -> tuple[FieldValidationIssue, ...]:
    relevant: list[FieldValidationIssue] = []
    for issue in issues:
        if issue.field_name == field_name:
            relevant.append(issue)
        elif field_name == "district" and issue.rule_id == "cross_field.city_district":
            relevant.append(issue)
    return tuple(relevant)


def _worst_eligibility_rank(issues: tuple[FieldValidationIssue, ...]) -> int:
    if not issues:
        return 0
    return max(ELIGIBILITY_RANK.get(issue.normalization_eligibility, 3) for issue in issues)


def _structural_penalty(field_name: str, value: str | None) -> int:
    if value is None or not value.strip():
        return 0

    penalty = 0
    stripped = value.strip()
    if CORRUPTION_MARKER_PATTERN.search(stripped):
        penalty += 50

    if field_name in {"company", "address", "city", "district", "first_name", "last_name"}:
        hyphen_count = stripped.count("-")
        space_count = stripped.count(" ")
        if hyphen_count >= 2 and hyphen_count > space_count:
            penalty += 15
        if ",," in stripped or "--" in stripped:
            penalty += 10

    if field_name == "company" and stripped.count(".") > 3:
        penalty += 5
    if field_name == "company" and COMPANY_SUFFIX_COMMA_CORRUPTION.search(stripped):
        penalty += 25

    if field_name in {"first_name", "last_name"}:
        if len(stripped) > 40:
            penalty += 20
        if PERSON_FIELD_LEGAL_SUFFIX.search(stripped):
            penalty += 30

    if field_name == "address":
        if ADDRESS_CAD_COMMA.search(stripped):
            penalty += 10
        if ADDRESS_TRAILING_LEGAL_SUFFIX.search(stripped):
            penalty += 20

    return penalty


def _identity_bonus(
    field_name: str,
    raw_value: str | None,
    issues: tuple[FieldValidationIssue, ...],
) -> int:
    if raw_value is None or not raw_value.strip():
        return 0

    bonus = 0
    stripped = raw_value.strip()

    if field_name == "phone":
        if TR_E164_PATTERN.match(stripped):
            bonus += 100
        elif not any(issue.rule_id == "phone.tr_e164" for issue in issues):
            bonus += 40
        elif not any(issue.rule_id == "phone.format" for issue in issues):
            bonus += 20

    if field_name == "email":
        if not any(issue.rule_id == "email.syntax" for issue in issues):
            bonus += 80

    if field_name == "city" and not any(issue.rule_id == "location.city_known" for issue in issues):
        bonus += 40

    if field_name == "district":
        if not any(issue.rule_id == "location.district_known" for issue in issues):
            bonus += 30
        if not any(issue.rule_id == "cross_field.city_district" for issue in issues):
            bonus += 20

    if field_name == "company" and not any(
        issue.rule_id == "company.min_length" for issue in issues
    ):
        bonus += 20

    if field_name == "address" and not any(
        issue.rule_id == "address.min_length" for issue in issues
    ):
        bonus += 15

    return bonus


def _selection_reason(
    *,
    field_name: str,
    quality: FieldCandidateQuality,
    distinct_normalized: int,
) -> str:
    if not quality.is_present:
        return "No non-empty values available."
    if distinct_normalized > 1:
        base = "Conflicting normalized values; selected highest-quality candidate"
    else:
        base = "Selected highest-quality candidate"
    return (
        f"{base} (errors={quality.error_count}, warnings={quality.warning_count}, "
        f"structural_penalty={quality.structural_penalty}, "
        f"source={quality.candidate.record.source_name})."
    )


def _source_diversity_by_normalized_value(
    records: list[EntityRecord],
    field_name: str,
) -> dict[str, int]:
    if field_name in IDENTITY_FIELDS:
        return {}

    sources_by_value: dict[str, set[str]] = {}
    for record in records:
        normalized = _normalize_field(field_name, record.get(field_name))
        if normalized is None:
            continue
        sources_by_value.setdefault(normalized, set()).add(record.source_name)
    return {value: len(sources) for value, sources in sources_by_value.items()}


def assess_field_candidate(
    record: EntityRecord,
    field_name: str,
    raw_value: str | None,
    *,
    config: SurvivorshipConfig,
    source_diversity_support: int = 0,
) -> FieldCandidateQuality:
    normalized = _normalize_field(field_name, raw_value)
    candidate = FieldCandidate(
        record=record,
        field_name=field_name,
        raw_value=raw_value,
        normalized_value=normalized,
    )
    is_present = normalized is not None

    if not is_present:
        return FieldCandidateQuality(
            candidate=candidate,
            is_present=False,
            error_count=999,
            warning_count=999,
            structural_penalty=999,
            eligibility_rank=99,
            identity_bonus=0,
            information_length=0,
            source_diversity_support=0,
            source_priority=config.source_priority.get(record.source_name, 999),
            record_id=record.record_id,
            selection_reason="Missing or blank value.",
        )

    context = _context_record(record, field_name, raw_value)
    validation = _validation_engine().validate_record(context)
    field_issues = _issues_for_field(validation.issues, field_name)
    error_count = sum(1 for item in field_issues if item.severity == Severity.ERROR)
    warning_count = sum(1 for item in field_issues if item.severity == Severity.WARNING)
    structural = _structural_penalty(field_name, raw_value)
    eligibility = _worst_eligibility_rank(field_issues)
    identity_bonus = _identity_bonus(field_name, raw_value, field_issues)

    return FieldCandidateQuality(
        candidate=candidate,
        is_present=True,
        error_count=error_count,
        warning_count=warning_count,
        structural_penalty=structural,
        eligibility_rank=eligibility,
        identity_bonus=identity_bonus,
        information_length=len(normalized or ""),
        source_diversity_support=source_diversity_support,
        source_priority=config.source_priority.get(record.source_name, 999),
        record_id=record.record_id,
        selection_reason="",
    )


def rank_field_candidates(
    records: list[EntityRecord],
    field_name: str,
    *,
    config: SurvivorshipConfig,
) -> list[FieldCandidateQuality]:
    diversity = _source_diversity_by_normalized_value(records, field_name)
    assessed = []
    for record in sorted(records, key=lambda item: item.record_id):
        normalized = _normalize_field(field_name, record.get(field_name))
        assessed.append(
            assess_field_candidate(
                record,
                field_name,
                record.get(field_name),
                config=config,
                source_diversity_support=diversity.get(normalized, 0)
                if normalized is not None
                else 0,
            )
        )
    return sorted(assessed, key=lambda item: item.ranking_tuple)


def select_best_candidate(
    records: list[EntityRecord],
    field_name: str,
    *,
    config: SurvivorshipConfig,
    distinct_normalized: int,
) -> FieldCandidateQuality | None:
    ranked = rank_field_candidates(records, field_name, config=config)
    present = [item for item in ranked if item.is_present]
    if not present:
        return ranked[0] if ranked else None
    best = present[0]
    return FieldCandidateQuality(
        candidate=best.candidate,
        is_present=best.is_present,
        error_count=best.error_count,
        warning_count=best.warning_count,
        structural_penalty=best.structural_penalty,
        eligibility_rank=best.eligibility_rank,
        identity_bonus=best.identity_bonus,
        information_length=best.information_length,
        source_diversity_support=best.source_diversity_support,
        source_priority=best.source_priority,
        record_id=best.record_id,
        selection_reason=_selection_reason(
            field_name=field_name,
            quality=best,
            distinct_normalized=distinct_normalized,
        ),
    )
