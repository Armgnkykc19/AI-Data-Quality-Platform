"""The session lifecycle: create, resolve, touch, revoke.

Four operations, and keeping them four is a design decision rather than an
accident of factoring.

In particular **resolving a session does not renew it**. It would be easy to
treat every authenticated request as activity and update the row inside
``resolve_session``, and it would be wrong in two ways. It would turn every
read into a write, on a single-connection SQLite database where writes
serialise; and it would make "which requests keep a session alive" a fact
buried in a lookup rather than a decision the HTTP layer states. A background
poll would silently keep an abandoned browser logged in forever.

So renewal is explicit. A later HTTP layer decides which requests count as
activity -- plausibly the user's own "Continue", plausibly any authenticated
request, plausibly neither -- and that decision is visible at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from identity.clock import (
    Clock,
    format_utc_timestamp,
    parse_utc_timestamp,
    system_utc_now,
    utc_now,
)
from identity.errors import (
    IdentityNotFoundError,
    IdentityValidationError,
    InactiveUserError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevokedError,
)
from identity.ids import new_session_id
from identity.models import User, UserStatus
from identity.repository import SessionRepository, UserReader
from identity.sessions import (
    DEFAULT_SESSION_POLICY,
    CreatedSession,
    Session,
    SessionPolicy,
    hash_session_token,
    new_session_token,
)

__all__ = ["ResolvedSession", "SessionService"]


@dataclass(frozen=True)
class ResolvedSession:
    """A valid session and the user it belongs to.

    Carries no organization and no role, because a session has neither. A
    caller that needs to know what this user may do reads current membership
    at that moment; this object only establishes who is asking.
    """

    session: Session
    user: User


class SessionService:
    """Owns every transition a session can make."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        users: UserReader,
        policy: SessionPolicy = DEFAULT_SESSION_POLICY,
        clock: Clock = system_utc_now,
    ) -> None:
        self._sessions = sessions
        self._users = users
        self._policy = policy
        self._clock = clock

    @property
    def policy(self) -> SessionPolicy:
        """The timing policy, so a later API can publish it instead of restating it."""
        return self._policy

    # -- creation -----------------------------------------------------------

    def create_session(self, user_id: str) -> CreatedSession:
        """Mint a session for an active user, returning the raw token once.

        At creation time ``T``::

            created_at           = T
            last_activity_at     = T
            absolute_expires_at  = T + absolute_timeout
            idle_expires_at      = min(T + idle_timeout, absolute_expires_at)

        The ``min`` matters even here, not only on renewal: it is what makes
        "idle expiry never exceeds absolute expiry" true for the whole life of
        the row rather than only after the first touch.

        Membership is deliberately not consulted. A user who belongs to no
        organization gets a perfectly valid session and simply has nothing to
        read with it -- which is an authorization outcome, decided later, by
        code that can see the membership table.
        """
        user = self._require_active_user(user_id)

        now = utc_now(self._clock)
        absolute_expires = now + self._policy.absolute_timeout
        idle_expires = min(now + self._policy.idle_timeout, absolute_expires)

        raw_token = new_session_token()
        session = Session(
            session_id=new_session_id(),
            user_id=user.user_id,
            token_hash=hash_session_token(raw_token),
            created_at_utc=format_utc_timestamp(now),
            last_activity_at_utc=format_utc_timestamp(now),
            idle_expires_at_utc=format_utc_timestamp(idle_expires),
            absolute_expires_at_utc=format_utc_timestamp(absolute_expires),
        )
        stored = self._sessions.create_session(session)
        # The raw token leaves this method and is never derivable again: only
        # its digest was persisted.
        return CreatedSession(session=stored, raw_token=raw_token)

    # -- resolution ---------------------------------------------------------

    def resolve_session(self, raw_token: str) -> ResolvedSession:
        """Validate a bearer token. Reads only; writes nothing.

        Checked in order: stored, not revoked, not past its absolute bound, not
        past its idle bound, owner still active. The order is what makes the
        errors useful -- a revoked session reports revocation rather than the
        expiry it would also eventually have hit.

        An unknown, empty or malformed token all raise ``SessionNotFoundError``
        identically. Distinguishing "not a valid token format" from "a valid
        format that matches nothing" would confirm to a prober that their guess
        about the format was right.
        """
        session = self._lookup(raw_token)
        now = utc_now(self._clock)
        self._assert_usable(session, now)
        user = self._require_active_user(session.user_id, missing_is_not_found=False)
        return ResolvedSession(session=session, user=user)

    # -- renewal ------------------------------------------------------------

    def touch_session(self, raw_token: str) -> Session:
        """Record activity, extending the idle window but never the absolute one.

        At time ``T``::

            last_activity_at = T
            idle_expires_at  = min(T + idle_timeout, absolute_expires_at)

        ``absolute_expires_at`` is not in that list and never changes. That cap
        is the whole reason an absolute bound exists: without it, an attacker
        holding a stolen token would keep it alive indefinitely simply by using
        it, and the session would never end on its own.

        Refused when the session is already revoked or expired. A touch is
        renewal, not resurrection -- reviving a session that had ended would
        mean a token remained useful after the moment it was supposed to stop
        working.

        The repository applies the update conditionally in one transaction, so
        a check here that passed and a state that changed underneath produce a
        refusal rather than a bad write. When that happens the session is
        re-read and the real reason is raised.
        """
        session = self._lookup(raw_token)
        now = utc_now(self._clock)
        self._assert_usable(session, now)

        absolute_expires = self._absolute_expiry(session)
        idle_expires = min(now + self._policy.idle_timeout, absolute_expires)

        updated = self._sessions.touch_session(
            session.session_id,
            now_utc=format_utc_timestamp(now),
            idle_expires_at_utc=format_utc_timestamp(idle_expires),
        )
        if updated is None:
            # The conditional update matched nothing: the row moved between the
            # read above and the write. Re-read and report what it actually is,
            # rather than inventing a reason.
            self._assert_usable(self._lookup(raw_token), now)
            # Still usable, so the only remaining cause is a concurrent touch
            # that already recorded activity at or after this instant. Nothing
            # is owed: activity is already at least this recent.
            return self._lookup(raw_token)
        return updated

    # -- revocation ---------------------------------------------------------

    def revoke_session(self, raw_token: str) -> Session:
        """End a session immediately and permanently.

        Works on an expired session as well as a live one, because "revoke it"
        is an operator instruction about a row, not a request that needs the
        session to still be usable.

        Idempotent: revoking twice leaves the first timestamp in place, because
        that is the moment access actually ended. The row is never deleted --
        a revoked session is evidence, and deleting it would destroy the record
        that it was cut short.
        """
        session = self._lookup(raw_token)
        revoked = self._sessions.revoke_session(
            session.session_id,
            revoked_at_utc=format_utc_timestamp(utc_now(self._clock)),
        )
        if revoked is None:  # pragma: no cover - the lookup just returned it
            raise SessionNotFoundError("The session could not be revoked because it is not stored.")
        return revoked

    # -- shared checks ------------------------------------------------------

    def _lookup(self, raw_token: str) -> Session:
        try:
            token_hash = hash_session_token(raw_token)
        except IdentityValidationError as exc:
            # An empty or non-string token. Reported as "no session", not as a
            # validation error: a prober must not learn that their guess was
            # the wrong *shape* rather than the wrong value.
            raise SessionNotFoundError("No session matches that token.") from exc
        session = self._sessions.get_session_by_token_hash(token_hash)
        if session is None:
            raise SessionNotFoundError("No session matches that token.")
        return session

    @staticmethod
    def _assert_usable(session: Session, now: datetime) -> None:
        """Raise unless this session may be used at ``now``.

        Boundary semantics are ``>=``: a session whose window ends at exactly
        this instant has ended. See ``Session.is_idle_expired``.
        """
        if session.is_revoked:
            raise SessionRevokedError("The session was revoked.")
        if session.is_absolutely_expired(now):
            raise SessionExpiredError(
                "The session reached its maximum lifetime.", reason="absolute"
            )
        if session.is_idle_expired(now):
            raise SessionExpiredError("The session expired after inactivity.", reason="idle")

    @staticmethod
    def _absolute_expiry(session: Session) -> datetime:
        return parse_utc_timestamp(session.absolute_expires_at_utc)

    def _require_active_user(self, user_id: str, *, missing_is_not_found: bool = True) -> User:
        """The owner must exist and be ACTIVE, at creation and at every use.

        Checked on resolution as well as on creation, which is what makes
        disabling an account take effect on the next request instead of
        whenever that person's session happened to expire.
        """
        user = self._users.get_user(user_id)
        if user is None:
            if missing_is_not_found:
                raise IdentityNotFoundError(f"User {user_id} is not stored.")
            raise SessionNotFoundError("The session's user no longer exists.")
        if user.status is not UserStatus.ACTIVE:
            raise InactiveUserError(f"User {user_id} is not active.")
        return user
