from __future__ import annotations

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.engine import resolve_entities
from survivorship.config import load_survivorship_config
from survivorship.engine import build_canonical_entities
from tests.survivorship.conftest import make_record


def test_survivorship_output_is_deterministic():
    resolution_config = load_entity_resolution_config()
    survivorship_config = load_survivorship_config()
    records = [
        make_record("a-1", email="one@example.com"),
        make_record("a-2", email="one@example.com"),
        make_record("b-1", source_name="source_b", email="two@example.com"),
        make_record("b-2", source_name="source_b", email="two@example.com"),
    ]
    resolution = resolve_entities(records, config=resolution_config)
    first = build_canonical_entities(resolution, config=survivorship_config)
    second = build_canonical_entities(resolution, config=survivorship_config)

    assert [entity.entity_id for entity in first.entities] == [
        entity.entity_id for entity in second.entities
    ]
    assert [entity.field_values for entity in first.entities] == [
        entity.field_values for entity in second.entities
    ]
