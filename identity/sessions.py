"""Opaque session tokens, the timing policy, and the stored session record.

The token is a bearer secret and nothing else. It carries no user id, no
organization, no role, no timestamp, no signature and no claims -- there is
nothing in it to read, so there is nothing in it to forge or to trust. Every
fact about a session is read from the row the token hashes to, at the moment it
is used, which is what lets a revocation take effect immediately and a role
change take effect without waiting for anything to expire.

That is the property a signed token (a JWT) gives up. A self-describing token
is authoritative until it expires, so revoking one means maintaining a denylist
-- which is a session table with extra steps and a window during which the
holder of a stolen token is still whoever the token says they are.

Two different hashes appear in this sprint and they are not interchangeable:

* A **password** is low-entropy and human-chosen, so it needs a deliberately
  slow, salted, memory-hard verifier. That is Argon2id, in
  ``identity.passwords``.
* A **session token** is 256 bits from a CSPRNG. There is nothing to brute
  force and nothing to rainbow-table, so a fast digest is not merely adequate
  but correct: SHA-256, unsalted, deterministic, because the digest has to be
  an index lookup key. Running Argon2 per request to find a session row would
  be a self-inflicted denial of service.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from identity.clock import parse_utc_timestamp
from identity.errors import IdentityValidationError
from identity.ids import SESSION_ID_PREFIX, USER_ID_PREFIX, assert_opaque_id

__all__ = [
    "DEFAULT_SESSION_POLICY",
    "SESSION_TOKEN_BYTES",
    "CreatedSession",
    "Session",
    "SessionPolicy",
    "hash_session_token",
    "new_session_token",
]

# 32 bytes = 256 bits from the OS CSPRNG. token_urlsafe renders them as ~43
# base64url characters, which is cookie-safe, header-safe and URL-safe without
# any escaping -- so no layer between the browser and this code has a reason to
# transform the value it is comparing.
SESSION_TOKEN_BYTES = 32

# Lowercase hex rather than a BLOB. Every other column in this schema is TEXT,
# a 64-character digest is fixed width so the UNIQUE index stays uniform, and
# an operator debugging with the sqlite3 shell sees a value they can match
# against a computed digest without decoding anything.
_TOKEN_HASH_LENGTH = 64


def new_session_token() -> str:
    """Mint one bearer token from the OS CSPRNG.

    ``secrets``, never ``random``: the latter is a Mersenne Twister whose
    entire future output is recoverable from 624 observed values. Never a
    UUID1, which encodes a MAC address and a timestamp. Never the user id with
    a random suffix, which tells an attacker which account a captured token
    belongs to before they have used it.
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(raw_token: str) -> str:
    """The stored lookup key for a raw token.

    Deterministic and unsalted by necessity: this value is what a lookup is
    performed *against*, so a per-row salt would mean scanning every session
    row and hashing once per row to find a match.

    That is safe here only because the input is uniformly random and 256 bits
    wide. The same construction applied to a password would be indefensible.
    """
    if not isinstance(raw_token, str) or not raw_token:
        raise IdentityValidationError("session token must be a non-empty string.")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionPolicy:
    """How long a session lives, in one object with one owner.

    Three bounds, and they answer different questions:

    * ``idle_timeout`` -- how long an unattended session stays usable. This is
      what protects an unlocked, walked-away-from laptop.
    * ``absolute_timeout`` -- how long a session may live at all, however
      active. This is what bounds a *stolen* token, which an attacker would
      otherwise keep alive indefinitely by using it.
    * ``warning_after`` -- when a frontend should warn that idleness is about
      to end it. This is UX and the server never enforces it, but it lives here
      so the later frontend and the later ``/session`` response read one value
      instead of each hard-coding 55.

    Keeping ``warning_after`` server-side also keeps the warning honest: a
    frontend that computed its own threshold could drift from the expiry the
    server actually applies, and warn after the session was already dead.
    """

    idle_timeout: timedelta = timedelta(minutes=60)
    warning_after: timedelta = timedelta(minutes=55)
    absolute_timeout: timedelta = timedelta(hours=8)

    def __post_init__(self) -> None:
        if self.warning_after <= timedelta(0):
            raise IdentityValidationError("warning_after must be positive.")
        if self.warning_after >= self.idle_timeout:
            raise IdentityValidationError(
                "warning_after must be shorter than idle_timeout; a warning that fires "
                "at or after expiry warns about something that already happened."
            )
        if self.idle_timeout >= self.absolute_timeout:
            raise IdentityValidationError(
                "idle_timeout must be shorter than absolute_timeout; otherwise the "
                "absolute bound could never be the one that ends a session."
            )

    @property
    def warning_window(self) -> timedelta:
        """How long the warning is on screen before idle expiry ends the session."""
        return self.idle_timeout - self.warning_after


DEFAULT_SESSION_POLICY = SessionPolicy()


@dataclass(frozen=True)
class Session:
    """One stored session. Identity only -- no tenant, no role, no permissions.

    The absence of an ``organization_id`` or a ``role`` column is the design.
    A role snapshotted at login would stay in force for up to eight hours after
    an operator removed it, which is exactly the revocation gap sessions exist
    to avoid. Authorization reads current membership at the moment of the
    request; a session answers only "which user is this".

    ``token_hash`` is excluded from ``repr`` for the same reason a password
    hash is: it is the live lookup key for an active session, and a log line
    carrying it points at a specific person's current login.
    """

    session_id: str
    user_id: str
    token_hash: str = field(repr=False)
    created_at_utc: str
    last_activity_at_utc: str
    idle_expires_at_utc: str
    absolute_expires_at_utc: str
    revoked_at_utc: str | None = None

    def __post_init__(self) -> None:
        assert_opaque_id(self.session_id, prefix=SESSION_ID_PREFIX, field_name="session_id")
        assert_opaque_id(self.user_id, prefix=USER_ID_PREFIX, field_name="user_id")
        if len(self.token_hash) != _TOKEN_HASH_LENGTH:
            raise IdentityValidationError(
                f"token_hash must be a {_TOKEN_HASH_LENGTH}-character digest."
            )
        for name in (
            "created_at_utc",
            "last_activity_at_utc",
            "idle_expires_at_utc",
            "absolute_expires_at_utc",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise IdentityValidationError(f"{name} must be a non-empty string.")
        if self.revoked_at_utc is not None and not str(self.revoked_at_utc).strip():
            raise IdentityValidationError("revoked_at_utc must be None or a non-empty string.")
        if self.idle_expires_at_utc > self.absolute_expires_at_utc:
            # The invariant a renewal must never break. Stated on the object as
            # well as in the schema, so a Session assembled in memory cannot
            # describe a state the database would refuse.
            raise IdentityValidationError(
                "idle_expires_at_utc must not exceed absolute_expires_at_utc."
            )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at_utc is not None

    def is_idle_expired(self, now: datetime) -> bool:
        """Expired *at* the boundary, not after it.

        ``>=`` rather than ``>`` throughout. A session whose idle window ends
        at exactly this instant has ended; treating the boundary instant as
        still-valid would make the meaning of "60 minutes" depend on clock
        resolution, and would leave a one-second window nobody intended.
        """
        return now >= parse_utc_timestamp(self.idle_expires_at_utc)

    def is_absolutely_expired(self, now: datetime) -> bool:
        return now >= parse_utc_timestamp(self.absolute_expires_at_utc)

    def is_valid_at(self, now: datetime) -> bool:
        return not (self.is_revoked or self.is_absolutely_expired(now) or self.is_idle_expired(now))


@dataclass(frozen=True)
class CreatedSession:
    """A freshly created session, plus the one copy of its raw token.

    The raw token exists in this object and nowhere else: it is never stored,
    never logged, and never recoverable from the database. A caller that loses
    it has to create a new session.

    ``repr=False`` on ``raw_token`` is load-bearing rather than tidy. The
    default dataclass ``repr`` is what an exception renderer, a debugger, a
    ``print`` during development and a structured logger all reach for, and any
    one of those would otherwise write a live bearer credential somewhere it
    outlives the request.
    """

    session: Session
    raw_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.raw_token, str) or not self.raw_token:
            raise IdentityValidationError("raw_token must be a non-empty string.")
        if hash_session_token(self.raw_token) != self.session.token_hash:
            raise IdentityValidationError(
                "raw_token does not hash to the session's stored token_hash."
            )
