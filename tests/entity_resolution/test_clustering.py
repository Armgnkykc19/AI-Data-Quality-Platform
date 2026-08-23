from __future__ import annotations

from entity_resolution.clustering import build_entity_clusters
from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from entity_resolution.models import EntityRecord, MatchDecisionType


def _record(record_id: str, **fields: str) -> EntityRecord:
    base = {
        "first_name": "Ali",
        "last_name": "Kaya",
        "email": f"{record_id}@example.com",
        "phone": "+905321234567",
        "company": "Acme",
        "city": "Ankara",
        "district": "Cankaya",
        "address": "Street 1",
    }
    base.update(fields)
    return EntityRecord(record_id=record_id, source_name="source_a", field_values=base)


def test_transitive_conflict_guard_blocks_unsafe_cluster():
    config = load_entity_resolution_config()
    a = _record("a-1", email="shared@example.com", phone="+905321111111")
    b = _record("a-2", email="shared@example.com", phone="+905321111111")
    c = _record("a-3", email="different@example.com", phone="+905329999999")

    result = resolve_entities([a, b, c], config=config)
    auto_edges = [
        decision
        for decision in result.decisions
        if decision.decision == MatchDecisionType.AUTO_MATCH
    ]
    records_by_id = {record.record_id: record for record in result.records}
    clusters, _guarded = build_entity_clusters(auto_edges, records_by_id, config=config)
    member_sets = [set(cluster.member_record_ids) for cluster in clusters]
    assert {"a-1", "a-2", "a-3"} not in member_sets


def test_cluster_ordering_is_deterministic():
    config = load_entity_resolution_config()
    records = [
        _record("a-1", email="one@example.com"),
        _record("a-2", email="one@example.com"),
        _record("a-3", email="two@example.com"),
        _record("a-4", email="two@example.com"),
    ]
    first = resolve_entities(records, config=config)
    second = resolve_entities(records, config=config)
    assert [cluster.member_record_ids for cluster in first.clusters] == [
        cluster.member_record_ids for cluster in second.clusters
    ]
