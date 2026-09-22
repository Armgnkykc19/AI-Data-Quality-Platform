"""What happens when COMMIT itself fails, and why it matters here.

``COMMIT`` is a statement, and it can fail on its own: SQLITE_BUSY, a full
disk, an I/O error. When it does, the transaction is **still open**. Before
Sprint 14 Phase A the commit sat outside the ``try`` in
``ReviewDatabase.transaction``, so a failed commit propagated with the
transaction never unwound.

That is worse than it sounds because of what shares the connection. One
``ReviewDatabase`` holds a single ``sqlite3`` connection, and review, tenant,
credential and session storage are all built over it. A transaction left open
would make the next ``BEGIN`` fail with "cannot start a transaction within a
transaction" for every one of them -- so a transient disk error during one
resolution would take authentication down with it until the process restarted.

Fault injection is done by wrapping ``execute`` on the live connection and
failing only the COMMIT. That exercises the real path rather than a
reimplementation of it, and it deliberately does not assert on sqlite's
internals: what is pinned is the state the connection is left in and the fact
that the caller's error is the one that surfaces.
"""

from __future__ import annotations

import sqlite3

import pytest

from human_review.models import ReviewCase
from review_application.errors import ReviewPersistenceError
from review_persistence.identity_schema import ORGANIZATIONS_TABLE
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository


class _CommitFailure(sqlite3.OperationalError):
    """Stands in for SQLITE_BUSY or a full disk at commit time."""


class _CommitFailingConnection:
    """A connection proxy that fails COMMIT on demand and nothing else.

    ``sqlite3.Connection.execute`` cannot be replaced on an instance, so the
    injection happens one level out. Everything except a failing COMMIT is
    delegated untouched -- including ``in_transaction``, which is what the
    assertions below actually read.
    """

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self._failures = 0

    def fail_next_commits(self, count: int) -> None:
        self._failures = count

    def stop_failing(self) -> None:
        self._failures = 0

    def execute(self, sql: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self._failures > 0 and sql.strip().upper().startswith("COMMIT"):
            self._failures -= 1
            raise _CommitFailure("disk I/O error")
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _FaultInjectingDatabase(ReviewDatabase):
    """A ``ReviewDatabase`` whose connection can be told to fail its COMMITs.

    Subclassed rather than patched so the transaction machinery under test is
    the real one: ``transaction``, ``_commit``, ``_abort`` and ``_discard`` all
    run exactly as they do in production.
    """

    def _open(self) -> sqlite3.Connection:
        return _CommitFailingConnection(super()._open())  # type: ignore[return-value]

    @property
    def proxy(self) -> _CommitFailingConnection:
        connection = self.connect()
        assert isinstance(connection, _CommitFailingConnection)
        return connection


@pytest.fixture
def faulty_database(persistence_config, clock):  # type: ignore[no-untyped-def]
    """An initialized database that commits normally until told otherwise.

    Schema creation itself commits, so failure injection is armed only after
    the database is open and valid.
    """
    database = _FaultInjectingDatabase(persistence_config, clock=clock)
    database.initialize()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def faulty_repository(faulty_database, clock):  # type: ignore[no-untyped-def]
    from tests.review_persistence.conftest import provision_queue

    queue = provision_queue(faulty_database)
    return SqliteReviewCaseRepository(
        faulty_database, review_queue_id=queue.review_queue_id, clock=clock
    )


def test_a_failed_commit_leaves_no_open_transaction(
    faulty_repository: SqliteReviewCaseRepository,
    faulty_database: _FaultInjectingDatabase,
    review_case: ReviewCase,
) -> None:
    """The invariant. After a failed COMMIT the connection is not mid-transaction."""
    faulty_database.proxy.fail_next_commits(1)

    with pytest.raises(_CommitFailure):
        faulty_repository.register_case(review_case)

    faulty_database.proxy.stop_failing()
    assert faulty_database.connect().in_transaction is False


def test_the_next_transaction_still_works_after_a_failed_commit(
    faulty_repository: SqliteReviewCaseRepository,
    faulty_database: _FaultInjectingDatabase,
    review_case: ReviewCase,
) -> None:
    """The failure this guards against: a poisoned connection.

    "cannot start a transaction within a transaction" is what the next writer
    used to get, on any table, until the process restarted.
    """
    faulty_database.proxy.fail_next_commits(1)
    with pytest.raises(_CommitFailure):
        faulty_repository.register_case(review_case)
    faulty_database.proxy.stop_failing()

    stored = faulty_repository.register_case(review_case)

    assert stored.review_case_id == review_case.review_case_id
    assert faulty_repository.get_case(review_case.review_case_id).case == review_case


def test_a_failed_commit_writes_nothing(
    faulty_repository: SqliteReviewCaseRepository,
    faulty_database: _FaultInjectingDatabase,
    review_case: ReviewCase,
) -> None:
    """Atomicity is unchanged: the abandoned unit of work left no row behind."""
    faulty_database.proxy.fail_next_commits(1)
    with pytest.raises(_CommitFailure):
        faulty_repository.register_case(review_case)
    faulty_database.proxy.stop_failing()

    assert faulty_repository.list_cases() == ()


def test_the_commit_failure_is_the_error_the_caller_sees(
    faulty_repository: SqliteReviewCaseRepository,
    faulty_database: _FaultInjectingDatabase,
    review_case: ReviewCase,
) -> None:
    """Cleanup must not replace the diagnosis.

    The rollback is best-effort precisely so a secondary failure cannot mask
    the reason the unit of work was abandoned -- an operator needs to see the
    disk error, not a complaint about rolling back.
    """
    faulty_database.proxy.fail_next_commits(1)

    with pytest.raises(_CommitFailure, match="disk I/O error"):
        faulty_repository.register_case(review_case)

    faulty_database.proxy.stop_failing()


def test_other_storage_over_the_same_connection_still_works(
    faulty_repository: SqliteReviewCaseRepository,
    faulty_database: _FaultInjectingDatabase,
    review_case: ReviewCase,
) -> None:
    """The blast radius this closes: one shared connection, four kinds of data.

    A review write that failed to commit must not stop the tenant and identity
    tables -- and therefore authentication -- from being usable.
    """
    faulty_database.proxy.fail_next_commits(1)
    with pytest.raises(_CommitFailure):
        faulty_repository.register_case(review_case)
    faulty_database.proxy.stop_failing()

    with faulty_database.transaction() as connection:
        rows = connection.execute(f"SELECT COUNT(*) AS n FROM {ORGANIZATIONS_TABLE}").fetchone()

    assert rows["n"] >= 1


# --------------------------------------------------------------------------
# Reentrancy, which the serialized resolution scope depends on
# --------------------------------------------------------------------------


def test_nested_transactions_commit_once_at_the_outermost_scope(
    database: ReviewDatabase,
) -> None:
    """Only the outer block ends the transaction; the inner one joins it."""
    with database.transaction() as outer:
        assert outer.in_transaction is True
        with database.transaction() as inner:
            assert inner is outer
            assert inner.in_transaction is True
        # Still open: the inner block did not commit.
        assert outer.in_transaction is True

    assert database.connect().in_transaction is False


def test_an_inner_failure_cannot_be_committed_by_an_outer_block(
    database: ReviewDatabase,
    review_case: ReviewCase,
    repository: SqliteReviewCaseRepository,
) -> None:
    """A swallowed inner error must still abandon the whole unit of work.

    The outer block below genuinely catches the inner exception and then exits
    normally, so nothing propagates to signal that anything went wrong. The
    failure is *latched* rather than inferred from what propagated, which is
    what stops the outer block from committing half a unit of work.

    And it is not silent: the rollback is reported, because returning quietly
    would tell the caller their write succeeded.
    """
    with pytest.raises(ReviewPersistenceError, match="suppressed"):
        with database.transaction():
            repository.register_case(review_case)
            try:
                with database.transaction():
                    raise ReviewPersistenceError("inner unit failed")
            except ReviewPersistenceError:
                pass  # deliberately swallowed, as a careless caller would

    assert repository.list_cases() == ()
    assert database.connect().in_transaction is False


def test_a_write_transaction_cannot_be_opened_inside_a_read_transaction(
    database: ReviewDatabase,
) -> None:
    """Refused rather than attempted.

    SQLite cannot promote a DEFERRED read snapshot to a write lock without
    risking a snapshot conflict, so the upgrade is rejected explicitly instead
    of failing later in a way that is hard to attribute.
    """
    with database.read_transaction():
        with pytest.raises(ReviewPersistenceError, match="read-only transaction"):
            with database.transaction():
                pass

    assert database.connect().in_transaction is False
