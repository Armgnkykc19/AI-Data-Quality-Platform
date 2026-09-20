"""SQLite storage for the session lifecycle.

The only interesting statement here is the touch, and it is interesting because
it is a conditional update rather than a read followed by a write.

The service reads a session, decides it is usable, computes a new idle expiry
and asks for the write. Between the read and the write, another request may
have revoked the session, or a concurrent touch may have already recorded later
activity. So the ``WHERE`` clause restates every condition the service checked,
and ``rowcount`` is the verdict: the update applies only against the state it
was computed for, or it applies to nothing at all.

That is what makes the three guarantees true rather than likely -- activity
never moves backwards, an expired session is never revived, and a revoked
session is never touched.

Timestamp comparisons happen in SQL as string comparisons, which is exact
because every stamp this project writes goes through one formatter producing a
fixed-width, ``Z``-suffixed, second-resolution value. Lexicographic order is
chronological order for those strings. See ``identity.clock``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from identity.errors import DuplicateIdentityError, IdentityNotFoundError
from identity.sessions import Session
from review_persistence.identity_schema import USER_SESSIONS_TABLE, USERS_TABLE
from review_persistence.sqlite.database import Clock, ReviewDatabase, _now

__all__ = ["SqliteSessionRepository"]

_COLUMNS = (
    "session_id",
    "user_id",
    "token_hash",
    "created_at_utc",
    "last_activity_at_utc",
    "idle_expires_at_utc",
    "absolute_expires_at_utc",
    "revoked_at_utc",
)

_INSERT = (
    f"INSERT INTO {USER_SESSIONS_TABLE} ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM {USER_SESSIONS_TABLE}"

# The conditional renewal. Every predicate after the primary key restates
# something the service already checked, because "already checked" describes a
# moment that has passed by the time this runs.
#
# * ``revoked_at_utc IS NULL`` -- revocation wins over a concurrent touch.
# * ``last_activity_at_utc <= ?`` -- activity is monotonic; a request that was
#   slow to arrive cannot drag it backwards to when it started.
# * ``idle_expires_at_utc > ?`` and ``absolute_expires_at_utc > ?`` -- a touch
#   renews a live session and never resurrects a dead one. Strict ``>``,
#   matching the ``>=``-means-expired boundary the service applies.
#
# ``absolute_expires_at_utc`` is absent from the SET clause and must stay
# absent. It is the bound that exists precisely so that activity cannot extend
# it.
_TOUCH = (
    f"UPDATE {USER_SESSIONS_TABLE} SET "
    "last_activity_at_utc = ?, idle_expires_at_utc = ? "
    "WHERE session_id = ? "
    "AND revoked_at_utc IS NULL "
    "AND last_activity_at_utc <= ? "
    "AND idle_expires_at_utc > ? "
    "AND absolute_expires_at_utc > ?"
)

# Idempotent by construction: the first revocation timestamp stands, because it
# is the moment access actually ended. Re-revoking is a no-op rather than an
# error, and nothing is ever deleted.
_REVOKE = (
    f"UPDATE {USER_SESSIONS_TABLE} SET revoked_at_utc = ? "
    "WHERE session_id = ? AND revoked_at_utc IS NULL"
)

_SELECT_USER = f"SELECT 1 FROM {USERS_TABLE} WHERE user_id = ?"


def _session_from_row(row: Mapping[str, Any]) -> Session:
    return Session(
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        token_hash=str(row["token_hash"]),
        created_at_utc=str(row["created_at_utc"]),
        last_activity_at_utc=str(row["last_activity_at_utc"]),
        idle_expires_at_utc=str(row["idle_expires_at_utc"]),
        absolute_expires_at_utc=str(row["absolute_expires_at_utc"]),
        revoked_at_utc=None if row["revoked_at_utc"] is None else str(row["revoked_at_utc"]),
    )


class SqliteSessionRepository:
    """Reads and writes ``user_sessions``.

    Satisfies ``identity.repository.SessionRepository`` structurally without
    inheriting it, matching the Protocol style used elsewhere in this project.
    """

    def __init__(self, database: ReviewDatabase, *, clock: Clock = _now) -> None:
        self._database = database
        self._clock = clock

    def create_session(self, session: Session) -> Session:
        """Store one new session.

        The user's existence is checked explicitly so a bad id reports a
        missing user rather than a constraint name; the foreign key is still
        what enforces it. A duplicate token hash is a genuine contradiction --
        two sessions cannot share a bearer token -- and is refused rather than
        merged.
        """
        with self._database.transaction() as connection:
            if connection.execute(_SELECT_USER, (session.user_id,)).fetchone() is None:
                raise IdentityNotFoundError(
                    f"User {session.user_id} is not stored; a session cannot belong to nobody."
                )
            try:
                connection.execute(
                    _INSERT,
                    (
                        session.session_id,
                        session.user_id,
                        session.token_hash,
                        session.created_at_utc,
                        session.last_activity_at_utc,
                        session.idle_expires_at_utc,
                        session.absolute_expires_at_utc,
                        session.revoked_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # Neither the session id nor the token digest is echoed: the
                # first identifies a live session, the second is its lookup key.
                raise DuplicateIdentityError(
                    "A session with that identifier or token already exists."
                ) from exc
        return session

    def get_session_by_token_hash(self, token_hash: str) -> Session | None:
        row = (
            self._database.connect()
            .execute(_SELECT + " WHERE token_hash = ?", (token_hash,))
            .fetchone()
        )
        return None if row is None else _session_from_row(row)

    def get_session(self, session_id: str) -> Session | None:
        """Read by internal id. For operator tooling and tests, not for auth.

        The authentication path never uses this: a caller presents a token, not
        a session id, and a lookup by id would be a lookup by a value that is
        safe to log -- which is exactly why it must not be an access path.
        """
        row = (
            self._database.connect()
            .execute(_SELECT + " WHERE session_id = ?", (session_id,))
            .fetchone()
        )
        return None if row is None else _session_from_row(row)

    def touch_session(
        self,
        session_id: str,
        *,
        now_utc: str,
        idle_expires_at_utc: str,
    ) -> Session | None:
        """Apply the conditional renewal, or return None if it did not apply.

        The update and the re-read share one IMMEDIATE transaction, so the
        returned row is the row this write produced rather than whatever a
        later writer left behind.
        """
        with self._database.transaction() as connection:
            cursor = connection.execute(
                _TOUCH,
                (
                    now_utc,
                    idle_expires_at_utc,
                    session_id,
                    now_utc,
                    now_utc,
                    now_utc,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(_SELECT + " WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else _session_from_row(row)

    def revoke_session(self, session_id: str, *, revoked_at_utc: str) -> Session | None:
        """Revoke once and keep the row.

        An already-revoked session matches no row in the UPDATE and is simply
        re-read, so the original revocation timestamp survives: that is the
        moment access ended, and a later call did not change it.
        """
        with self._database.transaction() as connection:
            connection.execute(_REVOKE, (revoked_at_utc, session_id))
            row = connection.execute(_SELECT + " WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else _session_from_row(row)
