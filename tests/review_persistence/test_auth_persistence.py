"""Credential and session storage: the guarantees the database itself provides.

The service layer checks that a session is usable before renewing it, and the
lifecycle tests prove it does. This file tests the other half -- what happens
when the state changes *between* that check and the write, and what the schema
refuses outright.

That distinction matters because the service's checks describe a moment that
has already passed by the time the UPDATE runs. The conditional update and the
CHECK constraints are what make the invariants true rather than likely, and
they are exercised here by calling the repository directly with the arguments
a racing caller would have computed.
"""

from __future__ import annotations

import sqlite3

import pytest

from identity.credentials import PasswordCredential
from identity.errors import DuplicateIdentityError, IdentityNotFoundError
from identity.models import User
from identity.sessions import Session, hash_session_token, new_session_token
from review_persistence.identity_schema import (
    PASSWORD_CREDENTIALS_TABLE,
    USER_SESSIONS_TABLE,
)
from review_persistence.schema import ALL_TABLES, DATABASE_SCHEMA_VERSION
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.session_repository import SqliteSessionRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository

USER_ID = "USR-persistence-ada"
NOW = "2026-09-12T08:00:00Z"
HASH = "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHRzYWx0$ZGlnZXN0ZGlnZXN0"


@pytest.fixture
def tenants(database: ReviewDatabase) -> SqliteTenantRepository:
    return SqliteTenantRepository(database)


@pytest.fixture
def stored_user(tenants: SqliteTenantRepository) -> User:
    return tenants.create_user(
        User.create(
            email="ada@example.com",
            display_name="Ada",
            created_at_utc=NOW,
            user_id=USER_ID,
        )
    )


@pytest.fixture
def credentials(database: ReviewDatabase) -> SqliteCredentialRepository:
    return SqliteCredentialRepository(database)


@pytest.fixture
def sessions(database: ReviewDatabase) -> SqliteSessionRepository:
    return SqliteSessionRepository(database)


def make_session(
    *,
    session_id: str = "SES-persistence-one",
    raw_token: str | None = None,
    created: str = NOW,
    last_activity: str = NOW,
    idle_expires: str = "2026-09-12T09:00:00Z",
    absolute_expires: str = "2026-09-12T16:00:00Z",
    user_id: str = USER_ID,
) -> Session:
    return Session(
        session_id=session_id,
        user_id=user_id,
        token_hash=hash_session_token(raw_token or new_session_token()),
        created_at_utc=created,
        last_activity_at_utc=last_activity,
        idle_expires_at_utc=idle_expires,
        absolute_expires_at_utc=absolute_expires,
    )


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_a_fresh_database_contains_both_authentication_tables(
    database: ReviewDatabase,
) -> None:
    present = {
        row["name"]
        for row in database.connect().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }

    assert PASSWORD_CREDENTIALS_TABLE in present
    assert USER_SESSIONS_TABLE in present
    assert present == set(ALL_TABLES)


def test_the_schema_version_is_still_the_unreleased_two_zero_zero() -> None:
    """2.0.0 is extended in place, not bumped again.

    It has never shipped: it is being assembled on this feature branch and no
    database outside it declares it. Sprint 10 set the precedent explicitly
    when it added the workflow-context table to an unreleased 1.0.0 rather than
    inventing 1.1.0 -- a version bump exists so an *existing* database can be
    recognised and migrated, and there is no such database here.

    1.0.0 remains migration-required, because that one did ship.
    """
    from review_persistence.schema import (
        MIGRATION_REQUIRED_SCHEMA_VERSIONS,
        SUPPORTED_DATABASE_SCHEMA_VERSIONS,
    )

    assert DATABASE_SCHEMA_VERSION == "2.0.0"
    assert SUPPORTED_DATABASE_SCHEMA_VERSIONS == frozenset({"2.0.0"})
    assert MIGRATION_REQUIRED_SCHEMA_VERSIONS == frozenset({"1.0.0"})


def test_both_tables_reference_users_with_restrict(database: ReviewDatabase) -> None:
    for table in (PASSWORD_CREDENTIALS_TABLE, USER_SESSIONS_TABLE):
        keys = database.connect().execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert [(key[2], key[3], key[4], key[6]) for key in keys] == [
            ("users", "user_id", "user_id", "RESTRICT")
        ], table


def test_a_user_with_a_credential_or_a_session_cannot_be_deleted(
    database: ReviewDatabase,
    credentials: SqliteCredentialRepository,
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Deletion is not a supported operation anywhere in this database, and the
    foreign keys are what make that true rather than a convention."""
    credentials.set_credential(
        PasswordCredential(
            user_id=USER_ID, password_hash=HASH, created_at_utc=NOW, updated_at_utc=NOW
        )
    )
    sessions.create_session(make_session())

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute("DELETE FROM users WHERE user_id = ?", (USER_ID,))


def test_the_token_hash_is_unique(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Two sessions cannot share a bearer token. That would make one token
    resolve to two identities."""
    token = new_session_token()
    sessions.create_session(make_session(session_id="SES-one", raw_token=token))

    with pytest.raises(DuplicateIdentityError):
        sessions.create_session(make_session(session_id="SES-two", raw_token=token))


def test_a_duplicate_error_never_echoes_the_token_or_the_digest(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    token = new_session_token()
    session = make_session(session_id="SES-one", raw_token=token)
    sessions.create_session(session)

    with pytest.raises(DuplicateIdentityError) as failure:
        sessions.create_session(make_session(session_id="SES-two", raw_token=token))

    assert token not in str(failure.value)
    assert session.token_hash not in str(failure.value)


def test_the_schema_refuses_an_idle_expiry_beyond_the_absolute_bound(
    database: ReviewDatabase,
    stored_user: User,
) -> None:
    """The invariant a renewal must never break, enforced below the service.

    A row claiming validity past its absolute expiry could not be written even
    by code that skipped the service entirely.
    """
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {USER_SESSIONS_TABLE} (session_id, user_id, token_hash, "
            "created_at_utc, last_activity_at_utc, idle_expires_at_utc, "
            "absolute_expires_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "SES-bad",
                USER_ID,
                hash_session_token(new_session_token()),
                NOW,
                NOW,
                "2026-09-12T17:00:00Z",  # after the absolute bound below
                "2026-09-12T16:00:00Z",
            ),
        )


def test_the_schema_refuses_an_absolute_expiry_before_creation(
    database: ReviewDatabase,
    stored_user: User,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {USER_SESSIONS_TABLE} (session_id, user_id, token_hash, "
            "created_at_utc, last_activity_at_utc, idle_expires_at_utc, "
            "absolute_expires_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "SES-bad",
                USER_ID,
                hash_session_token(new_session_token()),
                NOW,
                NOW,
                "2026-09-12T07:00:00Z",
                "2026-09-12T07:00:00Z",  # before created_at
            ),
        )


def test_the_schema_refuses_activity_before_creation(
    database: ReviewDatabase,
    stored_user: User,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {USER_SESSIONS_TABLE} (session_id, user_id, token_hash, "
            "created_at_utc, last_activity_at_utc, idle_expires_at_utc, "
            "absolute_expires_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "SES-bad",
                USER_ID,
                hash_session_token(new_session_token()),
                NOW,
                "2026-09-12T07:00:00Z",  # before created_at
                "2026-09-12T09:00:00Z",
                "2026-09-12T16:00:00Z",
            ),
        )


def test_the_credential_schema_refuses_an_update_before_creation(
    database: ReviewDatabase,
    stored_user: User,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            f"INSERT INTO {PASSWORD_CREDENTIALS_TABLE} (user_id, password_hash, "
            "created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?)",
            (USER_ID, HASH, NOW, "2026-09-12T07:00:00Z"),
        )


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def test_a_credential_round_trips(
    credentials: SqliteCredentialRepository,
    stored_user: User,
) -> None:
    stored = credentials.set_credential(
        PasswordCredential(
            user_id=USER_ID, password_hash=HASH, created_at_utc=NOW, updated_at_utc=NOW
        )
    )

    assert credentials.get_credential(USER_ID) == stored
    assert stored.password_hash == HASH


def test_a_user_can_hold_only_one_credential_and_replacement_keeps_its_birthday(
    credentials: SqliteCredentialRepository,
    stored_user: User,
) -> None:
    """The primary key on user_id makes a second verifier unrepresentable.

    ``created_at_utc`` records when this user first gained a way to log in,
    which is the question an operator asks of it, so replacement preserves it.
    """
    credentials.set_credential(
        PasswordCredential(
            user_id=USER_ID, password_hash=HASH, created_at_utc=NOW, updated_at_utc=NOW
        )
    )

    later = "2026-09-13T08:00:00Z"
    replaced = credentials.set_credential(
        PasswordCredential(
            user_id=USER_ID,
            password_hash=HASH + "2",
            created_at_utc=later,
            updated_at_utc=later,
        )
    )

    assert replaced.password_hash == HASH + "2"
    assert replaced.created_at_utc == NOW
    assert replaced.updated_at_utc == later


def test_a_credential_for_an_unknown_user_is_refused(
    credentials: SqliteCredentialRepository,
) -> None:
    """A verifier must never belong to nobody."""
    with pytest.raises(IdentityNotFoundError):
        credentials.set_credential(
            PasswordCredential(
                user_id="USR-never-created",
                password_hash=HASH,
                created_at_utc=NOW,
                updated_at_utc=NOW,
            )
        )


def test_an_unknown_user_has_no_credential(credentials: SqliteCredentialRepository) -> None:
    assert credentials.get_credential("USR-never-created") is None


def test_the_credential_store_offers_no_way_to_enumerate_or_delete() -> None:
    """A store whose contents can be listed is a store that will be listed."""
    methods = {name for name in dir(SqliteCredentialRepository) if not name.startswith("_")}

    assert methods == {"get_credential", "set_credential"}


# --------------------------------------------------------------------------
# Session concurrency
# --------------------------------------------------------------------------


def test_a_stale_touch_cannot_drag_activity_backwards(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """A slow request must not rewind activity to when it started.

    Two requests arrive close together; the later one commits first. The
    earlier one then tries to write an older ``last_activity_at_utc``, and the
    ``last_activity_at_utc <= ?`` predicate refuses it -- otherwise a session
    could be aged backwards by traffic that was meant to keep it alive.
    """
    session = sessions.create_session(make_session())

    fresh = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T08:40:00Z",
        idle_expires_at_utc="2026-09-12T09:40:00Z",
    )
    assert fresh is not None

    stale = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T08:10:00Z",
        idle_expires_at_utc="2026-09-12T09:10:00Z",
    )

    assert stale is None
    current = sessions.get_session(session.session_id)
    assert current is not None
    assert current.last_activity_at_utc == "2026-09-12T08:40:00Z"
    assert current.idle_expires_at_utc == "2026-09-12T09:40:00Z"


def test_a_touch_at_exactly_the_recorded_activity_time_is_allowed(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """``<=``, not ``<``.

    Two requests inside the same second are ordinary at second resolution, and
    refusing the second would make renewal depend on how busy the server is.
    """
    session = sessions.create_session(make_session())

    again = sessions.touch_session(
        session.session_id,
        now_utc=NOW,
        idle_expires_at_utc="2026-09-12T09:00:00Z",
    )

    assert again is not None


def test_a_touch_cannot_revive_an_idle_expired_session(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Enforced in SQL, not only in the service.

    The session's idle window closed at 09:00; a renewal computed at 09:30 --
    by a caller whose check raced the clock -- matches no row.
    """
    session = sessions.create_session(make_session())

    revived = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T09:30:00Z",
        idle_expires_at_utc="2026-09-12T10:30:00Z",
    )

    assert revived is None
    current = sessions.get_session(session.session_id)
    assert current is not None
    assert current.idle_expires_at_utc == "2026-09-12T09:00:00Z"


def test_a_touch_cannot_revive_an_absolutely_expired_session(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    session = sessions.create_session(
        make_session(idle_expires="2026-09-12T16:00:00Z", absolute_expires="2026-09-12T16:00:00Z")
    )

    revived = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T16:30:00Z",
        idle_expires_at_utc="2026-09-12T16:30:00Z",
    )

    assert revived is None


def test_a_touch_can_never_write_an_idle_expiry_past_the_absolute_bound(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Belt and braces: even a caller that computed the cap wrongly is refused.

    The service applies ``min(now + idle, absolute)``. If it ever stopped
    doing so, the CHECK constraint turns the mistake into a failed write rather
    than a session that outlives its absolute bound.
    """
    session = sessions.create_session(make_session())

    with pytest.raises(sqlite3.IntegrityError):
        sessions.touch_session(
            session.session_id,
            now_utc="2026-09-12T08:30:00Z",
            idle_expires_at_utc="2026-09-12T17:00:00Z",  # past absolute 16:00
        )


def test_a_touch_never_alters_the_absolute_expiry(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    session = sessions.create_session(make_session())

    touched = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T08:30:00Z",
        idle_expires_at_utc="2026-09-12T09:30:00Z",
    )

    assert touched is not None
    assert touched.absolute_expires_at_utc == session.absolute_expires_at_utc


def test_revocation_beats_a_concurrent_touch(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Revocation must be final the moment it commits.

    A touch already in flight when a session is revoked matches no row, so it
    cannot keep a session alive that an operator just ended.
    """
    session = sessions.create_session(make_session())
    sessions.revoke_session(session.session_id, revoked_at_utc="2026-09-12T08:15:00Z")

    touched = sessions.touch_session(
        session.session_id,
        now_utc="2026-09-12T08:20:00Z",
        idle_expires_at_utc="2026-09-12T09:20:00Z",
    )

    assert touched is None


def test_revoking_twice_keeps_the_first_timestamp(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    session = sessions.create_session(make_session())

    first = sessions.revoke_session(session.session_id, revoked_at_utc="2026-09-12T08:15:00Z")
    second = sessions.revoke_session(session.session_id, revoked_at_utc="2026-09-12T08:45:00Z")

    assert first is not None
    assert second is not None
    assert second.revoked_at_utc == "2026-09-12T08:15:00Z"


def test_revoking_an_unknown_session_returns_none(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    assert sessions.revoke_session("SES-never-created", revoked_at_utc=NOW) is None


def test_a_session_for_an_unknown_user_is_refused(
    sessions: SqliteSessionRepository,
) -> None:
    with pytest.raises(IdentityNotFoundError):
        sessions.create_session(make_session(user_id="USR-never-created"))


def test_a_failed_touch_leaves_the_row_coherent(
    database: ReviewDatabase,
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """A refused write is a whole refused write.

    The CHECK fires inside the transaction, which rolls back -- so the session
    is exactly as it was, not half-renewed.
    """
    session = sessions.create_session(make_session())
    before = sessions.get_session(session.session_id)

    with pytest.raises(sqlite3.IntegrityError):
        sessions.touch_session(
            session.session_id,
            now_utc="2026-09-12T08:30:00Z",
            idle_expires_at_utc="2026-09-12T17:00:00Z",
        )

    assert sessions.get_session(session.session_id) == before
    # And the connection is still usable, so the rollback really happened.
    assert database.connect().execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 1


def test_an_expired_session_row_survives_being_read(
    sessions: SqliteSessionRepository,
    stored_user: User,
) -> None:
    """Nothing in this repository deletes. Purging is operator maintenance."""
    session = sessions.create_session(make_session())

    for _ in range(3):
        assert sessions.get_session_by_token_hash(session.token_hash) is not None

    assert sessions.get_session(session.session_id) is not None
    assert "delete" not in {
        name for name in dir(SqliteSessionRepository) if not name.startswith("_")
    }
