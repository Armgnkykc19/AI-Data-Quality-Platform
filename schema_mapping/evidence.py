from __future__ import annotations

import difflib

from profiling.models import ColumnProfile
from schema_mapping.aliases import exact_alias_match
from schema_mapping.config import SchemaMappingConfig
from schema_mapping.models import EvidenceType, MappingEvidence
from schema_mapping.preprocessing import normalize_header


def _pattern_ratio(profile: ColumnProfile | None, pattern_name: str) -> float:
    if profile is None:
        return 0.0
    for pattern in profile.patterns:
        if pattern.pattern_name == pattern_name:
            return pattern.match_ratio
    return 0.0


def collect_evidence(
    *,
    header: str,
    canonical_field: str,
    profile: ColumnProfile | None,
    config: SchemaMappingConfig,
) -> list[MappingEvidence]:
    evidence: list[MappingEvidence] = []
    normalized = normalize_header(header)

    alias_match = exact_alias_match(header, config)
    if alias_match == canonical_field:
        weight = config.evidence_weights["exact_alias"]
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.EXACT_ALIAS,
                value=1.0,
                weight=weight,
                contribution=weight,
                description=f"Exact alias match for canonical field '{canonical_field}'.",
                source="alias_config",
            )
        )

    alias_owner = exact_alias_match(header, config)
    header_tokens = normalized.split("_")

    if alias_owner is None and canonical_field in header_tokens and normalized != canonical_field:
        token_prefix = normalized.startswith(f"{canonical_field}_")
        token_suffix = normalized.endswith(f"_{canonical_field}")
        if token_prefix or token_suffix:
            weight = 0.58
            evidence.append(
                MappingEvidence(
                    evidence_type=EvidenceType.LEXICAL_SIMILARITY,
                    value=1.0,
                    weight=weight,
                    contribution=weight,
                    description=(f"Header contains canonical token '{canonical_field}'."),
                    source="header_token_match",
                )
            )

    canonical_ratio = difflib.SequenceMatcher(
        None,
        normalized,
        normalize_header(canonical_field),
    ).ratio()
    alias_best = 0.0
    for alias in config.aliases.get(canonical_field, ()):
        alias_best = max(
            alias_best,
            difflib.SequenceMatcher(None, normalized, normalize_header(alias)).ratio(),
        )
    lexical_value = max(canonical_ratio, alias_best)
    normalized_tokens = set(normalized.split("_"))
    canonical_tokens = set(normalize_header(canonical_field).split("_"))
    if normalized_tokens & canonical_tokens:
        lexical_value = max(lexical_value, 0.72)
    if canonical_field in normalized.split("_"):
        lexical_value = max(lexical_value, 0.86)
    if normalized.startswith(canonical_field + "_") or normalized.endswith("_" + canonical_field):
        lexical_value = max(lexical_value, 0.86)
    if lexical_value >= config.lexical_minimum:
        weight = config.evidence_weights["lexical_similarity"]
        contribution = round(lexical_value * weight, 6)
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.LEXICAL_SIMILARITY,
                value=lexical_value,
                weight=weight,
                contribution=contribution,
                description=(f"Lexical similarity between header and '{canonical_field}'."),
                source="difflib.SequenceMatcher",
            )
        )

    inferred_type = profile.type_inference.inferred_type if profile else "string"
    compatible_types = config.type_compatibility.get(canonical_field, frozenset({"string"}))
    if inferred_type in compatible_types:
        weight = config.evidence_weights["type_compatibility"]
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.TYPE_COMPATIBILITY,
                value=1.0,
                weight=weight,
                contribution=weight,
                description=(
                    f"Inferred type '{inferred_type}' is compatible with '{canonical_field}'."
                ),
                source="type_inference",
            )
        )
    elif profile is not None and profile.non_null_count > 0:
        weight = config.evidence_weights["type_incompatibility"]
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.TYPE_INCOMPATIBILITY,
                value=1.0,
                weight=weight,
                contribution=weight,
                description=(
                    f"Inferred type '{inferred_type}' is incompatible with '{canonical_field}'."
                ),
                source="type_inference",
            )
        )

    email_ratio = _pattern_ratio(profile, "email_like")
    phone_ratio = _pattern_ratio(profile, "phone_like")
    numeric_ratio = _pattern_ratio(profile, "numeric_like")

    if canonical_field == "email" and email_ratio >= config.pattern_dominance:
        weight = config.evidence_weights["pattern_email"]
        contribution = round(email_ratio * weight, 6)
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.PATTERN_EMAIL,
                value=email_ratio,
                weight=weight,
                contribution=contribution,
                description="Column values are predominantly email-like.",
                source="pattern_profiling",
            )
        )
    if canonical_field == "phone" and phone_ratio >= config.pattern_dominance:
        weight = config.evidence_weights["pattern_phone"]
        contribution = round(phone_ratio * weight, 6)
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.PATTERN_PHONE,
                value=phone_ratio,
                weight=weight,
                contribution=contribution,
                description="Column values are predominantly phone-like.",
                source="pattern_profiling",
            )
        )
    if (
        canonical_field == "email"
        and numeric_ratio >= 0.95
        and email_ratio < config.pattern_dominance
    ):
        weight = config.evidence_weights["pattern_numeric"]
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.PATTERN_NUMERIC,
                value=numeric_ratio,
                weight=weight,
                contribution=weight,
                description="Column values are predominantly numeric-like.",
                source="pattern_profiling",
            )
        )
    if (
        canonical_field == "phone"
        and numeric_ratio >= 0.95
        and phone_ratio < config.pattern_dominance
    ):
        weight = config.evidence_weights["pattern_numeric"]
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.PATTERN_NUMERIC,
                value=numeric_ratio,
                weight=weight,
                contribution=weight,
                description="Column values are predominantly numeric-like.",
                source="pattern_profiling",
            )
        )

    if profile is not None and profile.completeness_ratio >= 0.80:
        weight = config.evidence_weights["completeness"]
        contribution = round(profile.completeness_ratio * weight, 6)
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.COMPLETENESS,
                value=profile.completeness_ratio,
                weight=weight,
                contribution=contribution,
                description="Column completeness supports mapping confidence.",
                source="column_profile",
            )
        )

    if profile is not None and profile.uniqueness_ratio >= 0.90:
        weight = config.evidence_weights["uniqueness"]
        contribution = round(profile.uniqueness_ratio * weight, 6)
        evidence.append(
            MappingEvidence(
                evidence_type=EvidenceType.UNIQUENESS,
                value=profile.uniqueness_ratio,
                weight=weight,
                contribution=contribution,
                description="High uniqueness observed (weak supporting evidence).",
                source="column_profile",
            )
        )

    return evidence
