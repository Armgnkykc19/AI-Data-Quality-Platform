"""SQLite connection factory and schema initialization.

Three properties matter more than anything else in this module.

First, SQLite leaves ``PRAGMA foreign_keys`` OFF by default, and the setting is
per connection, not per database. Every ``REFERENCES`` clause in the Phase A
schema is inert on a connection that forgot it, so the pragma is applied in one
place -- :meth:`ReviewDatabase.connect` -- and nothing else opens a connection.

Second, transactions are reentrant and only the outermost block ends one. One
connection is shared by review, tenant, identity and session storage, so a
transaction that was left open -- by a nested block committing early, or by a
COMMIT that failed and was not cleaned up -- would break the next BEGIN on all
four until the process restarted. :meth:`_unit_of_work` is the single place
that opens, joins, commits or aborts, and a connection whose transaction state
cannot be re-established is closed rather than handed to the next caller.

Third, nothing here ever migrates. An unrecognized stored schema version fails
closed with ``ReviewSchemaVersionError``; it is never upgraded, overwritten, or
recovered by dropping tables. A review queue holds human decisions, so guessing
is worse than refusing to open.

That applies in full to a schema 1.0.0 database, which this build can name but
cannot serve: its review data predates tenant ownership and belongs to no
organization or queue, so opening it would mean inventing an owner. It raises
``ReviewSchemaMigrationRequiredError`` -- a ``ReviewSchemaVersionError``, so
every existing handler answers it unchanged -- and the message says plainly
that no migration exists rather than naming one that does not.

Note what :meth:`ReviewDatabase.initialize` does and does not do. It creates
the schema only for a file with none of our tables; an existing database is
version-checked and otherwise left exactly as it is. So a constraint added to
an unreleased version does not reach a file that was created before it, and the
version check cannot notice. ``verify-queue`` is what notices; see
``review_persistence.integrity``.
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
        # Nesting state for the reentrant transaction below. Depth counts the
        # live blocks, ``_writable`` records what the outermost one opened, and
        # ``_failed`` latches so an inner failure cannot be committed by an
        # outer block that caught it.
        self._depth = 0
        self._writable = False
        self._failed = False

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

        Reentrant: a nested call joins the transaction already in progress
        instead of opening a second one. That is what lets the application
        service hold one write lock across loading the authorization bundle,
        asking the Sprint 08 domain, and writing the result -- without every
        repository method having to know whether it was called first.
        """
        with self._unit_of_work(write=True) as connection:
            yield connection

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Read several tables as one coherent snapshot.

        A DEFERRED transaction fixes the read snapshot at its first statement
        and holds it until COMMIT. Loading the workflow context and the case
        set as two unrelated reads could observe a database state that never
        existed -- cases from after a write, context from before it -- and the
        authorization check built on it would be evaluating a graph nobody ever
        committed.

        Reentrant in the same way as :meth:`transaction`. Nested inside a write
        transaction it joins that one, so the read sees the uncommitted work of
        the block that owns it -- which is exactly what a load-then-write unit
        of work needs.
        """
        with self._unit_of_work(write=False) as connection:
            yield connection

    @contextmanager
    def _unit_of_work(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        """The one place a transaction is opened, joined, committed or aborted.

        Only the outermost block issues BEGIN and only it ends the transaction.
        An inner block that raises latches ``_failed``, so an outer block which
        swallowed that exception still cannot commit half a unit of work.
        """
        connection = self.connect()

        if self._depth:
            if write and not self._writable:
                # SQLite cannot promote a DEFERRED read snapshot to a write
                # lock without risking a snapshot conflict, so this is refused
                # rather than attempted. Callers that write must take the write
                # transaction first.
                raise ReviewPersistenceError(
                    "A write transaction cannot be opened inside a read-only transaction. "
                    "Open the write transaction first; SQLite cannot safely upgrade a "
                    "DEFERRED snapshot to a write lock."
                )
            self._depth += 1
            try:
                yield connection
            except BaseException:
                self._failed = True
                raise
            finally:
                self._depth -= 1
            return

        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN DEFERRED")
        self._depth = 1
        self._writable = write
        self._failed = False
        try:
            yield connection
        except BaseException:
            self._failed = True
            raise
        finally:
            self._depth = 0
            self._writable = False
            failed = self._failed
            self._failed = False
            if failed:
                self._abort(connection)
            else:
                self._commit(connection)

        if failed:
            # Reached only when an inner block failed and an outer block caught
            # the exception, then exited normally. The transaction was rolled
            # back above, so returning quietly here would report success for a
            # unit of work that wrote nothing. Say so instead.
            raise ReviewPersistenceError(
                "A nested unit of work failed and its exception was suppressed, so the "
                "whole transaction was rolled back. Nothing was written."
            )

    def _commit(self, connection: sqlite3.Connection) -> None:
        """Commit, and never leave the connection mid-transaction if that fails.

        COMMIT is a statement that can fail on its own -- SQLITE_BUSY, a full
        disk, an I/O error -- and when it does the transaction is still open.
        This connection is shared by review, tenant, identity and session
        storage alike, so a transaction left open here would make the next
        BEGIN on any of them fail with "cannot start a transaction within a
        transaction" until the process restarted.

        So a failed COMMIT is rolled back on a best-effort basis and the
        original failure is re-raised unchanged. The commit error is what the
        caller needs to see; a rollback problem must not replace it.
        """
        try:
            connection.execute("COMMIT")
        except BaseException:
            self._abort(connection)
            raise

    def _abort(self, connection: sqlite3.Connection) -> None:
        """End the transaction without raising anything of its own.

        Called while another exception is propagating, so it swallows rollback
        errors: replacing the caller's failure with a secondary one would hide
        the reason the unit of work was abandoned.

        A rollback can legitimately fail because SQLite already unwound the
        transaction itself, which leaves the connection perfectly usable. That
        is why the decision to discard is made from ``in_transaction`` rather
        than from the rollback raising -- only a connection genuinely still
        inside a transaction is unusable, and that one is closed so the next
        :meth:`connect` opens a clean one.
        """
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if connection.in_transaction:
            self._discard(connection)

    def _discard(self, connection: sqlite3.Connection) -> None:
        """Drop a connection whose transaction state is no longer known."""
        if self._connection is connection:
            self._connection = None
        try:
            connection.close()
        except sqlite3.Error:
            pass

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
        # Nesting state belongs to the connection that is now gone. Leaving it
        # set would make the next transaction on a reopened connection believe
        # it was nested inside one that no longer exists.
        self._depth = 0
        self._writable = False
        self._failed = False

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
