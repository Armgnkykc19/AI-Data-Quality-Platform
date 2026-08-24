from __future__ import annotations

from entity_resolution.config import BlockingStrategyConfig, EntityResolutionConfig
from entity_resolution.models import (
    BlockingReasonType,
    CandidateReason,
    EntityRecord,
    MatchCandidate,
    RecordPair,
)
from entity_resolution.similarity import normalize_email, normalize_phone, normalize_text


def _phone_last7_key(record: EntityRecord) -> str | None:
    normalized = normalize_phone(record.get("phone"))
    if normalized is None:
        return None
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) < 7:
        return None
    return digits[-7:]


def _build_blocking_key(
    record: EntityRecord,
    fields: tuple[str, ...],
    *,
    config: EntityResolutionConfig,
    strategy_id: str | None = None,
) -> str | None:
    if strategy_id == "phone_last7" and fields == ("phone",):
        key = _phone_last7_key(record)
        if key is None or len(key) < config.blocking_min_key_length:
            return None
        return key

    parts: list[str] = []
    for field_name in fields:
        value = record.get(field_name)
        if field_name == "email":
            normalized = normalize_email(value)
        elif field_name == "phone":
            normalized = normalize_phone(value)
        else:
            normalized = normalize_text(value)
        if normalized is None:
            return None
        parts.append(normalized)
    key = "|".join(parts)
    if len(key) < config.blocking_min_key_length:
        return None
    return key


def _pairs_from_bucket(
    record_ids: list[str],
    *,
    reason_type: BlockingReasonType,
    blocking_key: str,
) -> list[MatchCandidate]:
    if len(record_ids) < 2:
        return []

    sorted_ids = sorted(record_ids)
    candidates: list[MatchCandidate] = []
    reason = CandidateReason(
        reason_type=reason_type,
        blocking_key=blocking_key,
        description=(f"Records share blocking key '{blocking_key}' via {reason_type.value}."),
    )
    for left_index in range(len(sorted_ids)):
        for right_index in range(left_index + 1, len(sorted_ids)):
            pair = RecordPair.ordered(sorted_ids[left_index], sorted_ids[right_index])
            candidates.append(MatchCandidate(pair=pair, reasons=(reason,)))
    return candidates


def _strategy_reason_type(strategy: BlockingStrategyConfig) -> BlockingReasonType:
    return BlockingReasonType(strategy.reason)


def generate_candidates(
    records: list[EntityRecord],
    *,
    config: EntityResolutionConfig,
) -> tuple[MatchCandidate, ...]:
    candidate_map: dict[RecordPair, list[CandidateReason]] = {}

    for strategy in config.blocking_strategies:
        buckets: dict[str, list[str]] = {}
        reason_type = _strategy_reason_type(strategy)
        for record in records:
            key = _build_blocking_key(
                record,
                strategy.fields,
                config=config,
                strategy_id=strategy.strategy_id,
            )
            if key is None:
                continue
            buckets.setdefault(key, []).append(record.record_id)

        for blocking_key, record_ids in sorted(buckets.items()):
            for candidate in _pairs_from_bucket(
                record_ids,
                reason_type=reason_type,
                blocking_key=blocking_key,
            ):
                existing = candidate_map.setdefault(candidate.pair, [])
                existing.extend(candidate.reasons)

    ordered_pairs = sorted(
        candidate_map.items(),
        key=lambda item: (item[0].record_a_id, item[0].record_b_id),
    )
    return tuple(
        MatchCandidate(
            pair=pair,
            reasons=tuple(sorted(reasons, key=lambda reason: reason.reason_type.value)),
        )
        for pair, reasons in ordered_pairs
    )


def possible_pair_count(record_count: int) -> int:
    if record_count < 2:
        return 0
    return record_count * (record_count - 1) // 2


def candidate_reduction_ratio(
    *,
    record_count: int,
    candidate_count: int,
) -> float:
    possible = possible_pair_count(record_count)
    if possible == 0:
        return 1.0
    return 1.0 - (candidate_count / possible)
