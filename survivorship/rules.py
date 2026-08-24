from __future__ import annotations

from entity_resolution.models import EntityRecord
from entity_resolution.similarity import normalize_email, normalize_phone, normalize_text
from survivorship.candidate_quality import select_best_candidate
from survivorship.config import SurvivorshipConfig
from survivorship.models import FieldProvenance, PreservedFieldConflict, SurvivorshipStrategy


def _normalize_field(field_name: str, value: str | None) -> str | None:
    if field_name == "email":
        return normalize_email(value)
    if field_name == "phone":
        return normalize_phone(value)
    return normalize_text(value)


def _collect_field_values(
    records: list[EntityRecord],
    field_name: str,
) -> list[tuple[EntityRecord, str | None, str | None]]:
    collected: list[tuple[EntityRecord, str | None, str | None]] = []
    for record in sorted(records, key=lambda item: item.record_id):
        raw = record.get(field_name)
        normalized = _normalize_field(field_name, raw)
        collected.append((record, raw, normalized))
    return collected


def _detect_conflict(
    field_name: str,
    collected: list[tuple[EntityRecord, str | None, str | None]],
) -> PreservedFieldConflict | None:
    non_empty = [
        (record.record_id, raw, normalized)
        for record, raw, normalized in collected
        if normalized is not None
    ]
    distinct = {normalized for _, _, normalized in non_empty}
    if len(distinct) <= 1:
        return None
    return PreservedFieldConflict(
        field_name=field_name,
        values_by_record=tuple((record_id, raw) for record_id, raw, _ in non_empty),
        normalized_values=tuple(sorted(distinct)),
        description=(
            f"Multiple distinct normalized values for '{field_name}' across cluster members."
        ),
    )


def _provenance_from_selection(
    field_name: str,
    *,
    selected_raw: str | None,
    record: EntityRecord,
    rule: str,
    description: str,
) -> FieldProvenance:
    return FieldProvenance(
        field_name=field_name,
        source_record_id=record.record_id,
        source_name=record.source_name,
        source_value=selected_raw,
        selected_value=selected_raw,
        rule=rule,
        description=description,
    )


def _select_quality_first(
    field_name: str,
    records: list[EntityRecord],
    *,
    config: SurvivorshipConfig,
    rule: SurvivorshipStrategy,
    collected: list[tuple[EntityRecord, str | None, str | None]],
) -> tuple[str | None, FieldProvenance]:
    distinct = {normalized for _, _, normalized in collected if normalized is not None}
    best = select_best_candidate(
        records,
        field_name,
        config=config,
        distinct_normalized=len(distinct),
    )
    if best is None or not best.is_present:
        record = collected[0][0]
        return None, _provenance_from_selection(
            field_name,
            selected_raw=None,
            record=record,
            rule=rule.value,
            description="No non-empty values available.",
        )

    record = best.candidate.record
    raw = best.candidate.raw_value
    return raw, _provenance_from_selection(
        field_name,
        selected_raw=raw,
        record=record,
        rule=rule.value,
        description=best.selection_reason,
    )


def apply_field_survivorship(
    records: list[EntityRecord],
    *,
    config: SurvivorshipConfig,
) -> tuple[dict[str, str | None], tuple[FieldProvenance, ...], tuple[PreservedFieldConflict, ...]]:
    field_values: dict[str, str | None] = {}
    provenance: list[FieldProvenance] = []
    conflicts: list[PreservedFieldConflict] = []

    for field_name in config.identity_fields:
        collected = _collect_field_values(records, field_name)
        conflict = _detect_conflict(field_name, collected)
        if conflict is not None and config.preserve_field_conflicts:
            conflicts.append(conflict)

        strategy_name = config.field_rules[field_name].strategy
        if strategy_name in {
            SurvivorshipStrategy.IDENTITY_CONSENSUS.value,
            SurvivorshipStrategy.QUALITY_IDENTITY.value,
        }:
            rule = SurvivorshipStrategy.QUALITY_IDENTITY
        elif strategy_name == SurvivorshipStrategy.COMPLETENESS_LONGEST.value:
            rule = SurvivorshipStrategy.COMPLETENESS_LONGEST
        else:
            rule = SurvivorshipStrategy.QUALITY_FIRST

        if rule == SurvivorshipStrategy.COMPLETENESS_LONGEST:
            selected, prov = _select_completeness_longest_legacy(
                field_name, collected, config=config
            )
        else:
            selected, prov = _select_quality_first(
                field_name,
                records,
                config=config,
                rule=rule,
                collected=collected,
            )

        field_values[field_name] = selected
        provenance.append(prov)

    return field_values, tuple(provenance), tuple(conflicts)


def _select_completeness_longest_legacy(
    field_name: str,
    collected: list[tuple[EntityRecord, str | None, str | None]],
    *,
    config: SurvivorshipConfig,
) -> tuple[str | None, FieldProvenance]:
    non_empty = [
        (record, raw, normalized) for record, raw, normalized in collected if normalized is not None
    ]
    if not non_empty:
        record = collected[0][0]
        return None, _provenance_from_selection(
            field_name,
            selected_raw=None,
            record=record,
            rule=SurvivorshipStrategy.COMPLETENESS_LONGEST.value,
            description="No non-empty values available.",
        )

    chosen = min(
        non_empty,
        key=lambda item: (
            -len(item[2] or ""),
            config.source_priority.get(item[0].source_name, 999),
            item[0].record_id,
        ),
    )
    record, raw, _ = chosen
    return raw, _provenance_from_selection(
        field_name,
        selected_raw=raw,
        record=record,
        rule=SurvivorshipStrategy.COMPLETENESS_LONGEST.value,
        description="Legacy completeness_longest selection.",
    )
