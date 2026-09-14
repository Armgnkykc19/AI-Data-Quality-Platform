"""SQLite connection factory and schema initialization.

Two properties matter more than anything else in this module.

First, SQLite leaves ``PRAGMA foreign_keys`` OFF by default, and the setting is
per connection, not per database. Every ``REFERENCES`` clause in the Phase A
schema is inert on a connection that forgot it, so the pragma is applied in one
place -- :meth:`ReviewDatabase.connect` -- and nothing else opens a connection.

Second, the module never migrates. An unrecognized stored schema version fails
closed with ``ReviewSchemaVersionError``; it is never upgraded, overwritten, or
recovered by dropping tables. A review queue holds human decisions, so guessing
is worse than refusing to open.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from review_application.clock import Clock, system_utc_now, utc_timestamp
from review_application.errors import ReviewPersistenceError, ReviewSchemaVersionError
from review_persistence.config import SUPPORTED_JOURNAL_MODES, ReviewPersistenceConfig
from review_persistence.schema import (
    ALL_TABLES,
    DATABASE_SCHEMA_VERSION,
    SCHEMA_META_TABLE,
    SCHEMA_STATEMENTS,
    assert_supported_schema_version,
)

# The clock moved to review_application.clock so the application service can
# stamp a resolution without importing a storage backend. Re-exported under the
# names this package already uses, because a database still needs a clock and
# every caller here should get the same one.
_now = system_utc_now

__all__ = [
    "Clock",
    "ReviewDatabase",
    "open_review_database",
    "utc_timestamp",
]


class ReviewDatabase:
    """Owns the connection to one review queue database file.

    A single lazily opened connection is held for the process. The queue is a
    single-writer local database; pooling would add failure modes without
    buying anything, and an explicit :meth:`close` is what makes restart
    behaviour testable.
    """

    def __init__(self, config: ReviewPersistenceConfig, *, clock: Clock = _now) -> None:
        self._config = config
        self._clock = clock
        self._connection: sqlite3.Connection | None = None

    @property
    def config(self) -> ReviewPersistenceConfig:
        return self._config

    def connect(self) -> sqlite3.Connection:
        """Return the configured connection, opening it on first use."""
        if self._connection is None:
            self._connection = self._open()
        return self._connection

    def _open(self) -> sqlite3.Connection:
        path = self._config.database_path
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(
            path,
            timeout=self._config.busy_timeout_seconds,
            # isolation_level=None disables the driver's implicit BEGIN. Every
            # transaction below is opened by hand, so no write can depend on
            # sqlite3's autocommit heuristics.
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        self._apply_pragmas(connection)
        return connection

    def _apply_pragmas(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self._config.busy_timeout_ms)}")

        # Re-checked here even though the config validated it: this value is
        # interpolated into SQL, and a hand-built config must not be able to
        # reach that interpolation with arbitrary text.
        journal_mode = self._config.journal_mode
        if journal_mode not in SUPPORTED_JOURNAL_MODES:
            raise ReviewPersistenceError(f"Unsupported journal_mode '{journal_mode}'.")
        connection.execute(f"PRAGMA journal_mode = {journal_mode}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a unit of work inside one explicit IMMEDIATE transaction.

        IMMEDIATE takes the write lock up front, so a read-then-insert such as
        case registration cannot interleave with another writer between its two
        statements.
        """
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Read several tables as one coherent snapshot.

        A DEFERRED transaction fixes the read snapshot at its first statement
        and holds it until COMMIT. Loading the workflow context and the case
        set as two unrelated reads could observe a database state that never
        existed -- cases from after a write, context from before it -- and the
        authorization check built on it would be evaluating a graph nobody ever
        committed.
        """
        connection = self.connect()
        connection.execute("BEGIN DEFERRED")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def initialize(self) -> sqlite3.Connection:
        """Create the schema on a new database, or validate an existing one."""
        connection = self.connect()
        present = self._existing_tables(connection)

        if SCHEMA_META_TABLE in present:
            assert_supported_schema_version(self._read_schema_version(connection))
            return connection

        if present:
            # Our tables without our version marker: an unknown or partially
            # written database. Creating the rest would blend two schemas.
            raise ReviewSchemaVersionError(
                "Review database is missing its schema_meta version marker but already "
                f"contains {sorted(present)}. Refusing to open; no migration is performed."
            )

        self._create_schema(connection)
        return connection

    def _existing_tables(self, connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row["name"] for row in rows} & set(ALL_TABLES)

    def _read_schema_version(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            f"SELECT schema_version FROM {SCHEMA_META_TABLE} WHERE id = 1"
        ).fetchone()
        if row is None:
            raise ReviewSchemaVersionError(
                "Review database has a schema_meta table with no version row. "
                "Refusing to open a database of unknown provenance."
            )
        return str(row["schema_version"])

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        # SQLite DDL is transactional, so a failure part-way through leaves no
        # half-built schema for the next open to mistake for a known database.
        with self.transaction() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.execute(
                f"INSERT INTO {SCHEMA_META_TABLE} (id, schema_version, created_at_utc) "
                "VALUES (1, ?, ?)",
                (DATABASE_SCHEMA_VERSION, utc_timestamp(self._clock)),
            )

    def schema_version(self) -> str:
        return self._read_schema_version(self.connect())

    def foreign_keys_enabled(self) -> bool:
        return bool(self.connect().execute("PRAGMA foreign_keys").fetchone()[0])

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> ReviewDatabase:
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_review_database(
    config: ReviewPersistenceConfig,
    *,
    clock: Clock = _now,
) -> ReviewDatabase:
    """Open and validate a review database in one call."""
    database = ReviewDatabase(config, clock=clock)
    database.initialize()
    return database
