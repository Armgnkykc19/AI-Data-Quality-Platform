from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from review_application.errors import (
    ReviewSchemaMigrationRequiredError,
    ReviewSchemaVersionError,
)
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.schema import DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.database import ReviewDatabase, open_review_database


@pytest.fixture
def config(tmp_path: Path) -> ReviewPersistenceConfig:
    return ReviewPersistenceConfig(
        database_path=tmp_path / "versioned.db",
        busy_timeout_ms=1000,
        journal_mode="WAL",
    )


def _stamp_version(config: ReviewPersistenceConfig, version: str) -> None:
    connection = sqlite3.connect(config.database_path)
    try:
        connection.execute("UPDATE schema_meta SET schema_version = ?", (version,))
        connection.commit()
    finally:
        connection.close()


def test_known_schema_version_opens(config: ReviewPersistenceConfig) -> None:
    open_review_database(config).close()

    reopened = open_review_database(config)
    try:
        assert reopened.schema_version() == DATABASE_SCHEMA_VERSION
    finally:
        reopened.close()


@pytest.mark.parametrize("version", ["0.9.0", "1.0.1", "3.0.0", "", "not-a-version"])
def test_unknown_schema_version_fails_closed(config: ReviewPersistenceConfig, version: str) -> None:
    open_review_database(config).close()
    _stamp_version(config, version)

    with pytest.raises(ReviewSchemaVersionError, match="Unsupported"):
        open_review_database(config)


def test_unknown_version_is_not_silently_upgraded(config: ReviewPersistenceConfig) -> None:
    open_review_database(config).close()
    _stamp_version(config, "3.0.0")

    with pytest.raises(ReviewSchemaVersionError):
        open_review_database(config)

    # The refusal must leave the file exactly as it was found: no rewrite of
    # the marker, no recovery by dropping tables.
    connection = sqlite3.connect(config.database_path)
    try:
        stored = connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()

    assert stored == "3.0.0"
    assert "review_cases" in tables


def test_a_schema_1_0_0_database_demands_an_explicit_migration(
    config: ReviewPersistenceConfig,
) -> None:
    """The pre-tenancy schema is refused on open, and nothing is changed.

    A 1.0.0 database holds review cases that belong to no organization and no
    review queue. Opening it under 2.0.0 would mean inventing an owner for
    other people's human decisions, so the process refuses to start and waits
    for an operator to migrate it deliberately.
    """
    open_review_database(config).close()
    _stamp_version(config, "1.0.0")

    with pytest.raises(ReviewSchemaMigrationRequiredError) as failure:
        open_review_database(config)

    assert failure.value.stored_version == "1.0.0"
    assert failure.value.required_version == DATABASE_SCHEMA_VERSION

    # No upgrade, no rewrite of the marker, no table dropped.
    connection = sqlite3.connect(config.database_path)
    try:
        stored = connection.execute("SELECT schema_version FROM schema_meta").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()

    assert stored == "1.0.0"
    assert "review_cases" in tables


def test_the_migration_error_is_still_a_schema_version_error(
    config: ReviewPersistenceConfig,
) -> None:
    """So every handler written against the old error keeps answering.

    ``review_api.errors`` maps ``ReviewSchemaVersionError`` to a 503 with a
    static message. The migration case must not slip past that mapping into
    the catch-all and become a 500.
    """
    open_review_database(config).close()
    _stamp_version(config, "1.0.0")

    with pytest.raises(ReviewSchemaVersionError):
        open_review_database(config)


def test_missing_version_row_fails_closed(config: ReviewPersistenceConfig) -> None:
    open_review_database(config).close()

    connection = sqlite3.connect(config.database_path)
    try:
        connection.execute("DELETE FROM schema_meta")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ReviewSchemaVersionError, match="no version row"):
        open_review_database(config)


def test_our_tables_without_a_version_marker_fail_closed(
    config: ReviewPersistenceConfig,
) -> None:
    # A half-written or foreign database. Filling in the missing tables would
    # blend two schemas into one file.
    connection = sqlite3.connect(config.database_path)
    try:
        connection.execute("CREATE TABLE review_cases (review_case_id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ReviewSchemaVersionError, match="schema_meta"):
        open_review_database(config)


def test_unrelated_database_is_treated_as_new(config: ReviewPersistenceConfig) -> None:
    # Tables we do not own are none of our business; the schema is created
    # alongside them rather than refusing or dropping anything.
    connection = sqlite3.connect(config.database_path)
    try:
        connection.execute("CREATE TABLE somebody_elses (x TEXT)")
        connection.commit()
    finally:
        connection.close()

    database = open_review_database(config)
    try:
        assert database.schema_version() == DATABASE_SCHEMA_VERSION
        survivors = {
            row["name"]
            for row in database.connect().execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "somebody_elses" in survivors
    finally:
        database.close()


def test_initialization_is_idempotent(config: ReviewPersistenceConfig) -> None:
    database = ReviewDatabase(config)
    try:
        database.initialize()
        database.initialize()
        assert database.schema_version() == DATABASE_SCHEMA_VERSION
        count = database.connect().execute("SELECT COUNT(*) AS n FROM schema_meta").fetchone()
        assert count["n"] == 1
    finally:
        database.close()
