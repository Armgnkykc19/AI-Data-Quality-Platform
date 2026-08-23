from __future__ import annotations

from entity_resolution.config import EntityResolutionConfig
from entity_resolution.models import (
    ConflictType,
    EntityRecord,
    EvidenceType,
    PairConflict,
    PairEvidence,
)
from entity_resolution.similarity import (
    fuzzy_similarity,
    is_non_empty,
    normalize_email,
    normalize_phone,
    normalize_text,
)


def _exact_evidence(
    *,
    evidence_type: EvidenceType,
    field_name: str,
    left: str | None,
    right: str | None,
    weight: float,
    strength: str,
) -> PairEvidence | None:
    if not is_non_empty(left) or not is_non_empty(right):
        return None
    if left != right:
        return None
    return PairEvidence(
        evidence_type=evidence_type,
        field_name=field_name,
        value=1.0,
        weight=weight,
        contribution=weight,
        strength=strength,
        description=f"Exact match on {field_name}.",
    )


def _similarity_evidence(
    *,
    evidence_type: EvidenceType,
    field_name: str,
    left: str | None,
    right: str | None,
    weight: float,
    minimum: float,
    strength: str,
) -> PairEvidence | None:
    if not is_non_empty(left) or not is_non_empty(right):
        return None
    similarity = fuzzy_similarity(left, right)
    if similarity < minimum:
        return None
    return PairEvidence(
        evidence_type=evidence_type,
        field_name=field_name,
        value=similarity,
        weight=weight,
        contribution=weight * similarity,
        strength=strength,
        description=f"Fuzzy similarity on {field_name}: {similarity:.4f}.",
    )


def collect_pair_evidence(
    left: EntityRecord,
    right: EntityRecord,
    *,
    config: EntityResolutionConfig,
) -> tuple[PairEvidence, ...]:
    weights = config.evidence_weights
    evidence: list[PairEvidence] = []

    left_email = normalize_email(left.get("email"))
    right_email = normalize_email(right.get("email"))
    if left_email and right_email and left_email == right_email:
        weight = weights.get("email_exact", 0.45)
        evidence.append(
            PairEvidence(
                evidence_type=EvidenceType.EMAIL_EXACT,
                field_name="email",
                value=1.0,
                weight=weight,
                contribution=weight,
                strength="strong",
                description="Normalized email addresses are identical.",
            )
        )

    left_phone = normalize_phone(left.get("phone"))
    right_phone = normalize_phone(right.get("phone"))
    if left_phone and right_phone and left_phone == right_phone:
        weight = weights.get("phone_exact", 0.45)
        evidence.append(
            PairEvidence(
                evidence_type=EvidenceType.PHONE_EXACT,
                field_name="phone",
                value=1.0,
                weight=weight,
                contribution=weight,
                strength="strong",
                description="Normalized phone numbers are identical.",
            )
        )

    exact_specs = [
        (EvidenceType.FIRST_NAME_EXACT, "first_name", "first_name_exact", "moderate"),
        (EvidenceType.LAST_NAME_EXACT, "last_name", "last_name_exact", "moderate"),
        (EvidenceType.COMPANY_EXACT, "company", "company_exact", "moderate"),
        (EvidenceType.CITY_EXACT, "city", "city_exact", "weak"),
        (EvidenceType.DISTRICT_EXACT, "district", "district_exact", "weak"),
        (EvidenceType.ADDRESS_EXACT, "address", "address_exact", "moderate"),
    ]
    for evidence_type, field_name, weight_key, strength in exact_specs:
        left_value = normalize_text(left.get(field_name))
        right_value = normalize_text(right.get(field_name))
        item = _exact_evidence(
            evidence_type=evidence_type,
            field_name=field_name,
            left=left_value,
            right=right_value,
            weight=weights.get(weight_key, 0.0),
            strength=strength,
        )
        if item is not None:
            evidence.append(item)

    fuzzy_specs = [
        (EvidenceType.FIRST_NAME_SIMILARITY, "first_name", "first_name_similarity"),
        (EvidenceType.LAST_NAME_SIMILARITY, "last_name", "last_name_similarity"),
        (EvidenceType.COMPANY_SIMILARITY, "company", "company_similarity"),
        (EvidenceType.ADDRESS_SIMILARITY, "address", "address_similarity"),
    ]
    for evidence_type, field_name, weight_key in fuzzy_specs:
        item = _similarity_evidence(
            evidence_type=evidence_type,
            field_name=field_name,
            left=left.get(field_name),
            right=right.get(field_name),
            weight=weights.get(weight_key, 0.0),
            minimum=config.fuzzy_minimum,
            strength="moderate",
        )
        if item is not None:
            evidence.append(item)

    return tuple(sorted(evidence, key=lambda item: item.evidence_type.value))


def collect_pair_conflicts(
    left: EntityRecord,
    right: EntityRecord,
    *,
    config: EntityResolutionConfig,
) -> tuple[PairConflict, ...]:
    penalties = config.conflict_penalties
    conflicts: list[PairConflict] = []

    left_email = normalize_email(left.get("email"))
    right_email = normalize_email(right.get("email"))
    if left_email and right_email and left_email != right_email:
        conflicts.append(
            PairConflict(
                conflict_type=ConflictType.EMAIL_CONFLICT,
                field_name="email",
                severity="severe",
                penalty=penalties.get("email_conflict", 0.55),
                description="Both records have different non-empty email addresses.",
            )
        )

    left_phone = normalize_phone(left.get("phone"))
    right_phone = normalize_phone(right.get("phone"))
    if left_phone and right_phone and left_phone != right_phone:
        conflicts.append(
            PairConflict(
                conflict_type=ConflictType.PHONE_CONFLICT,
                field_name="phone",
                severity="severe",
                penalty=penalties.get("phone_conflict", 0.55),
                description="Both records have different non-empty phone numbers.",
            )
        )

    left_company = normalize_text(left.get("company"))
    right_company = normalize_text(right.get("company"))
    if left_company and right_company and left_company != right_company:
        similarity = fuzzy_similarity(left.get("company"), right.get("company"))
        if similarity < config.company_conflict_threshold:
            conflicts.append(
                PairConflict(
                    conflict_type=ConflictType.COMPANY_CONFLICT,
                    field_name="company",
                    severity="moderate",
                    penalty=penalties.get("company_conflict", 0.20),
                    description="Company names differ beyond similarity tolerance.",
                )
            )

    left_city = normalize_text(left.get("city"))
    right_city = normalize_text(right.get("city"))
    if left_city and right_city and left_city != right_city:
        conflicts.append(
            PairConflict(
                conflict_type=ConflictType.LOCATION_CONFLICT,
                field_name="city",
                severity="moderate",
                penalty=penalties.get("location_conflict", 0.15),
                description="City values differ.",
            )
        )

    return tuple(sorted(conflicts, key=lambda item: item.conflict_type.value))
