"""File-backed restart tests.

Deliberately not ``:memory:``. An in-memory database cannot demonstrate that
anything survives a process boundary, which is the only property these tests
exist to prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from entity_resolution.models import ResolutionResult
from human_review.models import ReviewCase, ReviewStatus, ReviewWorkflowState
from review_application.errors import (
    DuplicateCaseRegistrationError,
    ReviewWorkflowContextConflictError,
)
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.schema import DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.database import open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from tests.review_persistence.conftest import (
    FrozenClock,
    bound_repository,
    seed_resolved_case,
)


def _reopen(config: ReviewPersistenceConfig, clock: FrozenClock) -> SqliteReviewCaseRepository:
    """A fresh database object and connection against the same file."""
    return bound_repository(open_review_database(config, clock=clock), clock)


def test_registered_case_survives_a_full_restart(
    persistence_config: ReviewPersistenceConfig,
    review_case: ReviewCase,
) -> None:
    clock = FrozenClock()

    first_database = open_review_database(persistence_config, clock=clock)
    written = bound_repository(first_database, clock).register_case(review_case)
    first_database.close()

    assert persistence_config.database_path.exists()

    clock.advance(86400)
    second_database = open_review_database(persistence_config, clock=clock)
    try:
        reloaded = bound_repository(second_database, clock).get_case(review_case.review_case_id)
    finally:
        second_database.close()

    assert reloaded.case == review_case
    assert reloaded.review_case_id == review_case.review_case_id
    assert reloaded.version == 1
    assert reloaded.created_at_utc == written.created_at_utc
    assert reloaded.updated_at_utc == written.updated_at_utc
    assert reloaded.status is ReviewStatus.PENDING


def test_resolved_case_survives_a_restart_without_regressing(
    persistence_config: ReviewPersistenceConfig,
    resolved_match_case: ReviewCase,
    review_case: ReviewCase,
    review_state: ReviewWorkflowState,
    resolved_match_state: ReviewWorkflowState,
    resolution: ResolutionResult,
) -> None:
    clock = FrozenClock()

    database = open_review_database(persistence_config, clock=clock)
    written = seed_resolved_case(
        bound_repository(database, clock),
        pending_state=review_state,
        resolved_state=resolved_match_state,
        review_case_id=review_case.review_case_id,
        resolution=resolution,
    )
    database.close()

    clock.advance(3600)
    repository = _reopen(persistence_config, clock)
    try:
        # Case generation after a restart still emits the PENDING form.
        returned = repository.register_case(review_case)
        reloaded = repository.get_case(resolved_match_case.review_case_id)
    finally:
        repository._database.close()

    assert returned.status is ReviewStatus.MATCH
    assert reloaded.case == resolved_match_case
    assert reloaded.version == written.version
    assert reloaded.created_at_utc == written.created_at_utc
    assert reloaded.updated_at_utc == written.updated_at_utc


def test_schema_version_and_foreign_keys_survive_a_restart(
    persistence_config: ReviewPersistenceConfig,
    review_case: ReviewCase,
) -> None:
    clock = FrozenClock()
    database = open_review_database(persistence_config, clock=clock)
    bound_repository(database, clock).register_case(review_case)
    database.close()

    reopened = open_review_database(persistence_config, clock=clock)
    try:
        assert reopened.schema_version() == DATABASE_SCHEMA_VERSION
        assert reopened.foreign_keys_enabled() is True
    finally:
        reopened.close()


def test_identity_guard_still_applies_after_a_restart(
    persistence_config: ReviewPersistenceConfig,
    review_case: ReviewCase,
) -> None:
    import dataclasses

    clock = FrozenClock()
    database = open_review_database(persistence_config, clock=clock)
    bound_repository(database, clock).register_case(review_case)
    database.close()

    repository = _reopen(persistence_config, clock)
    impostor = dataclasses.replace(
        review_case,
        pair=dataclasses.replace(review_case.pair, record_b_id="z-999"),
    )
    try:
        with pytest.raises(DuplicateCaseRegistrationError):
            repository.register_case(impostor)
    finally:
        repository._database.close()


def test_no_database_is_created_outside_the_temporary_path(
    persistence_config: ReviewPersistenceConfig,
    review_case: ReviewCase,
    tmp_path: Path,
) -> None:
    database = open_review_database(persistence_config)
    try:
        bound_repository(database).register_case(review_case)
    finally:
        database.close()

    created = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert "review_queue.db" in created
    assert persistence_config.database_path.parent == tmp_path

    # The shipped default must not have been touched by any of this.
    from review_persistence.config import PROJECT_ROOT

    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()


def test_workflow_bundle_survives_a_full_restart(
    persistence_config: ReviewPersistenceConfig,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, object],
) -> None:
    """Context, cases, versions, records, and the AUTO_MATCH snapshot together."""
    clock = FrozenClock()

    database = open_review_database(persistence_config, clock=clock)
    written = bound_repository(database, clock).register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=snapshot,
        entity_resolution_config_path="configs/entity_resolution.yaml",
    )
    before = bound_repository(database, clock).load_workflow_bundle()
    database.close()

    clock.advance(86400)
    reopened = open_review_database(persistence_config, clock=clock)
    try:
        after = bound_repository(reopened, clock).load_workflow_bundle()
    finally:
        reopened.close()

    assert after == before
    assert after.persisted_cases == written
    assert after.entity_records == resolution.records
    assert after.resolution_snapshot == snapshot
    assert after.entity_resolution_config_path == "configs/entity_resolution.yaml"
    assert set(after.versions_by_case_id().values()) == {1}
    assert after.next_resolution_sequence == before.next_resolution_sequence


def test_resolved_workflow_survives_a_restart_without_regressing(
    persistence_config: ReviewPersistenceConfig,
    review_state: ReviewWorkflowState,
    resolved_match_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, object],
) -> None:
    clock = FrozenClock()

    database = open_review_database(persistence_config, clock=clock)
    written = seed_resolved_case(
        bound_repository(database, clock),
        pending_state=review_state,
        resolved_state=resolved_match_state,
        review_case_id=review_state.cases[0].review_case_id,
        resolution=resolution,
    )
    database.close()

    clock.advance(3600)
    reopened = open_review_database(persistence_config, clock=clock)
    try:
        repository = bound_repository(reopened, clock)
        # Case generation after a restart still emits the PENDING form.
        returned = repository.register_workflow(
            review_state,
            entity_records=resolution.records,
            resolution_snapshot=snapshot,
            entity_resolution_config_path=None,
        )
        bundle = repository.load_workflow_bundle()
    finally:
        reopened.close()

    assert returned[0].status is ReviewStatus.MATCH
    assert returned[0].version == written.version
    assert returned[0].created_at_utc == written.created_at_utc
    assert returned[0].updated_at_utc == written.updated_at_utc
    assert bundle.cases() == resolved_match_state.cases


def test_context_conflict_still_fails_closed_after_a_restart(
    persistence_config: ReviewPersistenceConfig,
    review_state: ReviewWorkflowState,
    resolution: ResolutionResult,
    snapshot: dict[str, object],
) -> None:
    clock = FrozenClock()
    database = open_review_database(persistence_config, clock=clock)
    bound_repository(database, clock).register_workflow(
        review_state,
        entity_records=resolution.records,
        resolution_snapshot=snapshot,
        entity_resolution_config_path=None,
    )
    database.close()

    reopened = open_review_database(persistence_config, clock=clock)
    try:
        repository = bound_repository(reopened, clock)
        with pytest.raises(ReviewWorkflowContextConflictError):
            repository.register_workflow(
                review_state,
                entity_records=resolution.records,
                resolution_snapshot={"source_label": "other", "auto_match_pairs": []},
                entity_resolution_config_path=None,
            )
    finally:
        reopened.close()
