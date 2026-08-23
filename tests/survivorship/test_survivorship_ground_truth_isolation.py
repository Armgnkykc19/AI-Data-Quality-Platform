from __future__ import annotations

import pytest

from entity_resolution.models import (
    EntityRecord,
    ResolutionResult,
    ResolutionSummary,
)
from survivorship.engine import build_canonical_entities


@pytest.fixture
def survivorship_config():
    from survivorship.config import load_survivorship_config

    return load_survivorship_config()


def test_engine_never_reads_person_id_from_records(survivorship_config):
    record = EntityRecord(
        record_id="x-1",
        source_name="source_a",
        field_values={
            "person_id": "P-999",
            "email": "x@example.com",
            "first_name": "Test",
            "last_name": "User",
            "phone": "+905321234567",
            "company": "Acme",
            "city": "Ankara",
            "district": "Cankaya",
            "address": "Street 1",
        },
    )
    resolution = ResolutionResult(
        source_label="inline",
        records=(record,),
        candidates=(),
        decisions=(),
        review_queue=(),
        clusters=(),
        summary=ResolutionSummary(
            record_count=1,
            possible_pair_count=0,
            candidate_pair_count=0,
            candidate_reduction_ratio=0.0,
            auto_match_count=0,
            review_count=0,
            no_match_count=0,
            cluster_count=0,
            conflict_guarded_clusters=0,
        ),
    )
    with pytest.raises(ValueError, match="person_id"):
        build_canonical_entities(resolution, config=survivorship_config)
