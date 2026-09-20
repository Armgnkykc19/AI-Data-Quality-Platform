from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from review_application.errors import ReviewPersistenceError
from review_application.queues import ReviewQueue
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.schema import ALL_TABLES, DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from tests.review_persistence.conftest import FrozenClock


def test_new_database_creates_the_schema_and_records_its_version(
    database: ReviewDatabase,
) -> None:
    assert database.schema_version() == DATABASE_SCHEMA_VERSION

    rows = (
        database.connect()
        .execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        .fetchall()
    )
    assert {row["name"] for row in rows} == set(ALL_TABLES)


def test_no_tables_beyond_the_approved_schema_are_created(database: ReviewDatabase) -> None:
    rows = (
        database.connect()
        .execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        .fetchall()
    )
    unexpected = {row["name"] for row in rows} - set(ALL_TABLES)
    assert not unexpected, f"Phase B introduced unapproved tables: {sorted(unexpected)}"


def test_schema_meta_holds_exactly_one_row(database: ReviewDatabase) -> None:
    rows = database.connect().execute("SELECT id, schema_version FROM schema_meta").fetchall()
    assert [(row["id"], row["schema_version"]) for row in rows] == [(1, DATABASE_SCHEMA_VERSION)]


def test_schema_meta_timestamp_comes_from_the_injected_clock(
    persistence_config: ReviewPersistenceConfig,
) -> None:
    clock = FrozenClock()
    database = open_review_database(persistence_config, clock=clock)
    try:
        stored = (
            database.connect()
            .execute("SELECT created_at_utc FROM schema_meta WHERE id = 1")
            .fetchone()
        )
        assert stored["created_at_utc"] == "2026-09-12T08:00:00Z"
    finally:
        database.close()


def test_foreign_keys_are_on_for_every_connection(
    persistence_config: ReviewPersistenceConfig,
) -> None:
    # The pragma is per connection, not per database, so a reopen must set it
    # again or every REFERENCES clause silently stops being enforced.
    first = open_review_database(persistence_config)
    try:
        assert first.foreign_keys_enabled() is True
    finally:
        first.close()

    second = open_review_database(persistence_config)
    try:
        assert second.foreign_keys_enabled() is True
    finally:
        second.close()


def test_foreign_keys_are_actually_enforced(database: ReviewDatabase) -> None:
    connection = database.connect()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO review_case_events ("
            "review_case_id, event_type, occurred_at_utc, schema_version"
            ") VALUES ('RC-missing', 'CASE_CREATED', '2026-09-12T08:00:00Z', ?)",
            (DATABASE_SCHEMA_VERSION,),
        )


def test_configured_busy_timeout_is_applied(persistence_config: ReviewPersistenceConfig) -> None:
    database = open_review_database(persistence_config)
    try:
        stored = database.connect().execute("PRAGMA busy_timeout").fetchone()[0]
        assert stored == persistence_config.busy_timeout_ms
    finally:
        database.close()


def test_configured_journal_mode_is_applied(tmp_path: Path) -> None:
    config = ReviewPersistenceConfig(
        database_path=tmp_path / "delete_mode.db",
        busy_timeout_ms=1000,
        journal_mode="DELETE",
    )
    database = open_review_database(config)
    try:
        mode = database.connect().execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.upper() == "DELETE"
    finally:
        database.close()


def test_connection_uses_a_named_row_factory(database: ReviewDatabase) -> None:
    row = database.connect().execute("SELECT schema_version FROM schema_meta").fetchone()
    assert isinstance(row, sqlite3.Row)
    assert row["schema_version"] == DATABASE_SCHEMA_VERSION


def test_transaction_rolls_back_on_failure(
    database: ReviewDatabase,
    review_queue: ReviewQueue,
) -> None:
    connection = database.connect()

    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO review_cases ("
                "review_queue_id, review_case_id, record_a_id, record_b_id, status, "
                "machine_decision, machine_score, version, case_payload_json, "
                "schema_version, created_at_utc, updated_at_utc"
                ") VALUES (?, 'RC-rollback', 'a-1', 'a-2', 'PENDING', 'REVIEW', 0.8, 1, "
                "'{}', ?, '2026-09-12T08:00:00Z', '2026-09-12T08:00:00Z')",
                (review_queue.review_queue_id, DATABASE_SCHEMA_VERSION),
            )
            raise RuntimeError("boom")

    remaining = connection.execute("SELECT COUNT(*) AS n FROM review_cases").fetchone()
    assert remaining["n"] == 0


def test_transaction_commits_on_success(database: ReviewDatabase) -> None:
    with database.transaction() as conn:
        conn.execute("UPDATE schema_meta SET created_at_utc = 'committed' WHERE id = 1")

    stored = (
        database.connect().execute("SELECT created_at_utc FROM schema_meta WHERE id = 1").fetchone()
    )
    assert stored["created_at_utc"] == "committed"


def test_driver_level_implicit_transactions_are_disabled(database: ReviewDatabase) -> None:
    # isolation_level=None means every BEGIN in this package is deliberate.
    assert database.connect().isolation_level is None


def test_parent_directory_is_created_on_demand(tmp_path: Path) -> None:
    config = ReviewPersistenceConfig(
        database_path=tmp_path / "nested" / "deeper" / "queue.db",
        busy_timeout_ms=1000,
        journal_mode="WAL",
    )
    database = open_review_database(config)
    try:
        assert config.database_path.exists()
    finally:
        database.close()


def test_journal_mode_is_revalidated_before_it_reaches_sql(tmp_path: Path) -> None:
    # A config built by hand bypasses the loader; the pragma interpolation must
    # still refuse anything outside the allow-list.
    config = ReviewPersistenceConfig(
        database_path=tmp_path / "q.db",
        busy_timeout_ms=1000,
        journal_mode="WAL",
    )
    object.__setattr__(config, "journal_mode", "WAL; DROP TABLE review_cases")

    with pytest.raises(ReviewPersistenceError, match="journal_mode"):
        ReviewDatabase(config).connect()


def test_context_manager_initializes_and_closes(
    persistence_config: ReviewPersistenceConfig,
) -> None:
    with ReviewDatabase(persistence_config) as database:
        assert database.schema_version() == DATABASE_SCHEMA_VERSION
        connection = database.connect()

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_reopening_an_existing_database_does_not_rewrite_it(
    persistence_config: ReviewPersistenceConfig,
) -> None:
    first = open_review_database(persistence_config, clock=FrozenClock())
    original = (
        first.connect()
        .execute("SELECT created_at_utc FROM schema_meta WHERE id = 1")
        .fetchone()["created_at_utc"]
    )
    first.close()

    later = FrozenClock()
    later.advance(3600)
    second = open_review_database(persistence_config, clock=later)
    try:
        reopened = (
            second.connect()
            .execute("SELECT created_at_utc FROM schema_meta WHERE id = 1")
            .fetchone()["created_at_utc"]
        )
    finally:
        second.close()

    assert reopened == original
