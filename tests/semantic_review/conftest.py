from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.models import EntityRecord
from human_review.cases import generate_review_cases
from semantic_review.config import load_semantic_review_config
from tests.human_review.conftest import make_review_resolution


@pytest.fixture
def resolution_config():
    return load_entity_resolution_config()


def frozen_clock() -> datetime:
    return datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def semantic_config(tmp_path):
    config = load_semantic_review_config()
    return replace(config, report_output_directory=tmp_path / "semantic_reports")


@pytest.fixture
def enabled_semantic_config(semantic_config):
    return replace(semantic_config, enabled=True)


def _with_shared_email(resolution):
    records = []
    for record in resolution.records:
        fields = dict(record.field_values)
        fields["email"] = "shared@example.com"
        records.append(
            EntityRecord(
                record_id=record.record_id,
                source_name=record.source_name,
                field_values=fields,
            )
        )
    return replace(resolution, records=tuple(records))


@pytest.fixture
def review_bundle(resolution_config):
    resolution = _with_shared_email(make_review_resolution("a-1", "a-2"))
    state = generate_review_cases(resolution, config=resolution_config)
    records_by_id = {record.record_id: record for record in resolution.records}
    return resolution, state, records_by_id, resolution_config
