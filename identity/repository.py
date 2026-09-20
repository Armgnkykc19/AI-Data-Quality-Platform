"""Persistence contracts for credentials and sessions.

Structural ``typing.Protocol``, matching ``ReviewCaseRepository`` and
``SemanticReviewProvider``. The services above these contracts never import a
storage module, so the authentication and session logic is testable against
in-memory fakes and would drive a different backend unchanged.

Both contracts are narrow on purpose. There is no generic ``update``, no
``delete_all``, no query builder and no filter argument: every method answers
one question something actually asks, and a primitive nobody needs is a
primitive that will eventually be used for something nobody reviewed.
"""

from __future__ import annotations

from typing import Protocol

from identity.credentials import PasswordCredential
from identity.models import User
from identity.sessions import Session

__all__ = [
    "CredentialRepository",
    "SessionRepository",
    "UserLookup",
    "UserProvisioningRepository",
    "UserReader",
]


class UserReader(Protocol):
    """The one thing the session service needs from the tenant graph.

    Narrower than the full tenant repository on purpose: creating a session
    requires knowing that a user exists and is active, and nothing else. A
    service that held the whole repository could read organizations and
    memberships, which is authorization material a session must never touch.
    """

    def get_user(self, user_id: str) -> User | None: ...


class UserLookup(Protocol):
    """The one thing the authentication service needs from the tenant graph.

    A lookup by normalized login handle, and nothing else. Naming a wider
    interface here would put organizations and memberships within reach of the
    login path, which must not decide anything about them.
    """

    def get_user_by_email(self, email: str) -> User | None: ...


class CredentialRepository(Protocol):
    """Storage for password verifiers. One active credential per user."""

    def get_credential(self, user_id: str) -> PasswordCredential | None:
        """Return the stored credential, or None when the user has none."""
        ...

    def set_credential(self, credential: PasswordCredential) -> PasswordCredential:
        """Store or replace this user's credential.

        Replacement rather than insert-only, because the two writers that exist
        -- operator provisioning and the rehash a successful login may perform
        -- both mean "this is now the verifier". There is no history: a kept
        collection of superseded verifiers is a kept collection of things worth
        attacking, and nothing in this product needs one.

        Must refuse a credential for a user that is not stored, so a verifier
        can never belong to nobody.
        """
        ...


class UserProvisioningRepository(Protocol):
    """Creating a user and their password as one unit.

    Separate from ``CredentialRepository`` because the guarantee is different:
    this one is about atomicity across two tables. An implementation must write
    both rows in a single transaction, so a failure can never leave a user who
    exists but cannot log in -- a state nothing errors on and nobody can see.
    """

    def create_user_with_credential(
        self,
        user: User,
        credential: PasswordCredential,
    ) -> User:
        """Store both, or neither. Refuses a duplicate login handle."""
        ...


class SessionRepository(Protocol):
    """Storage for the session lifecycle.

    Notice what is missing: no ``delete_expired``. Expiry is a validity
    decision made when a session is used, and deleting rows during
    authentication would tie a security decision to garbage collection and
    destroy the record of sessions that existed. Purging is operator
    maintenance, and it is not part of this contract.
    """

    def create_session(self, session: Session) -> Session:
        """Store a new session. Must refuse a duplicate token hash."""
        ...

    def get_session_by_token_hash(self, token_hash: str) -> Session | None:
        """Find one session by the digest of its bearer token."""
        ...

    def touch_session(
        self,
        session_id: str,
        *,
        now_utc: str,
        idle_expires_at_utc: str,
    ) -> Session | None:
        """Move activity forward, atomically, or return None.

        Returns None rather than raising when the write did not apply, because
        "did not apply" has several causes the caller is better placed to
        describe: the session was revoked, it had already expired, or a
        concurrent touch had already moved activity past ``now_utc``.

        An implementation must apply the update only when all of those hold,
        in one transaction with the read that checked them. In particular it
        must never move ``last_activity_at_utc`` backwards, never revive an
        expired session, and never alter ``absolute_expires_at_utc``.
        """
        ...

    def revoke_session(self, session_id: str, *, revoked_at_utc: str) -> Session | None:
        """Mark the session revoked, once. Returns None when it is not stored.

        Idempotent on an already-revoked session: the first revocation
        timestamp stands, because it is the one that describes when access
        actually ended. Never deletes the row.
        """
        ...
