"""Turning a login handle and a password into a verified identity.

This service answers exactly one question -- *who is this?* -- and refuses to
answer any part of the next one. It does not choose an organization, read a
membership, consult a role, or decide what the user may do. A user who belongs
to no organization at all authenticates perfectly well here; they simply have
nothing to look at afterwards, which is a matter for the authorization layer.

Keeping those apart is what lets a role change take effect immediately. If
login resolved permissions, they would be resolved once and then be stale for
as long as the resulting session lived.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from identity.clock import Clock, system_utc_now, utc_timestamp
from identity.credentials import PasswordCredential
from identity.errors import AuthenticationFailedError, IdentityValidationError
from identity.models import User, UserStatus
from identity.normalization import normalize_login_email
from identity.passwords import PasswordHasher
from identity.repository import CredentialRepository, UserLookup

__all__ = ["AuthenticatedUser", "AuthenticationService"]

# The generic answer, used for every failure cause. One string, defined once,
# so no branch can accidentally phrase its refusal differently and reintroduce
# the distinction the single error type exists to remove.
_GENERIC_FAILURE = "The credentials were not accepted."


@dataclass(frozen=True)
class AuthenticatedUser:
    """A verified identity, reduced to what anything downstream needs.

    ``user_id`` is the durable audit identity and ``display_name`` is what a
    later interface shows. The email address is deliberately not carried: the
    caller just supplied it, nothing downstream needs it, and an identity
    object that holds it is an identity object that will eventually be logged
    or serialised with it.
    """

    user_id: str
    display_name: str

    @classmethod
    def of(cls, user: User) -> AuthenticatedUser:
        return cls(user_id=user.user_id, display_name=user.display_name)


class AuthenticationService:
    """Verifies a password against a stored Argon2id credential."""

    def __init__(
        self,
        *,
        users: UserLookup,
        credentials: CredentialRepository,
        hasher: PasswordHasher,
        clock: Clock = system_utc_now,
    ) -> None:
        # ``UserLookup`` exposes exactly one method. A service holding the
        # whole tenant repository could read memberships, and the login path
        # must not be able to decide anything about them.
        self._users = users
        self._credentials = credentials
        self._hasher = hasher
        self._clock = clock
        self._dummy_hash: str | None = None

    def authenticate(self, login_handle: str, password: str) -> AuthenticatedUser:
        """Verify a login, or raise ``AuthenticationFailedError``.

        Four different causes -- unknown handle, wrong password, disabled
        account, missing credential -- produce the same exception with the same
        message, and all four perform a real Argon2id verification before they
        fail. See ``_reject`` for why the second half matters as much as the
        first.

        The login handle is normalized with the Phase B rule, so the lookup
        finds the same row the UNIQUE constraint would have refused a duplicate
        of. The **password is not normalized**: it is passed to the verifier
        exactly as supplied, because any transformation here would have to be
        reproduced identically forever, including against hashes written before
        it existed.
        """
        try:
            normalized = normalize_login_email(login_handle)
        except IdentityValidationError:
            # A handle that is not an address cannot match any stored user, but
            # returning early here would make a malformed address measurably
            # faster to reject than a well-formed unknown one.
            return self._reject(password)

        user = self._users.get_user_by_email(normalized)
        if user is None:
            return self._reject(password)

        credential = self._credentials.get_credential(user.user_id)
        if credential is None:
            return self._reject(password)

        if not self._hasher.verify(credential.password_hash, password):
            return self._reject(password)

        # Status is checked *after* verification rather than before. Checking
        # first would let a disabled account be identified by how quickly it
        # was refused, which is the same oracle in a different place.
        if user.status is not UserStatus.ACTIVE:
            raise AuthenticationFailedError(_GENERIC_FAILURE)

        self._rehash_if_needed(user, credential, password)
        return AuthenticatedUser.of(user)

    # -- enumeration defence ------------------------------------------------

    def _reject(self, password: str) -> AuthenticatedUser:
        """Do the work a successful login would have done, then fail.

        Without this, an unknown address is refused in microseconds while a
        known one costs a full Argon2id verification -- tens of milliseconds,
        by design. That difference is measurable over a handful of requests and
        turns the login endpoint into a "does this person have an account
        here?" API, which is worth a great deal to anyone assembling a target
        list.

        This is not a claim of constant time. Branch prediction, allocator
        behaviour, and the database lookup that did or did not find a row all
        still differ. The goal is narrower and achievable: remove the
        order-of-magnitude gap that makes the oracle trivial.

        The return type is a lie the type checker tolerates -- this never
        returns -- but it lets every call site read ``return self._reject(...)``
        and makes it obvious that no path continues past one.
        """
        self._hasher.verify(self._dummy_verifier(), password)
        raise AuthenticationFailedError(_GENERIC_FAILURE)

    def _dummy_verifier(self) -> str:
        """An Argon2id hash of a secret nobody knows, computed at most once.

        Computed lazily and cached: hashing on every failed login would make
        the failure path *more* expensive than the success path, which is the
        same timing signal inverted, and would hand an attacker a cheap way to
        burn the server's memory-hard budget.

        The secret is freshly random and never stored. Using a real user's
        password would mean every failed login in the process verified against
        a live credential, and a fixed literal would put a known plaintext for
        a known hash in the source.
        """
        if self._dummy_hash is None:
            self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))
        return self._dummy_hash

    # -- rehash -------------------------------------------------------------

    def _rehash_if_needed(
        self,
        user: User,
        credential: PasswordCredential,
        password: str,
    ) -> None:
        """Upgrade a verified credential whose parameters have since moved.

        Only ever reached after a successful verification, so this is the one
        moment the plaintext is available to produce a stronger hash with. A
        failed login never touches the credential.

        A storage failure here is allowed to propagate rather than being
        swallowed. The alternative -- returning success over a database that
        just refused a write -- would mint a session against storage that is
        not working, and would hide the failure from the operator who needs to
        see it. It stays distinguishable from a rejected credential because it
        is not an ``AuthenticationFailedError``.
        """
        if not self._hasher.needs_rehash(credential.password_hash):
            return
        # The policy is deliberately *not* re-applied here. It governs what
        # may be set, and this is a re-encoding of a secret that was already
        # accepted -- so a password that predates a tightened minimum keeps
        # working instead of locking out a user who did nothing wrong. Setting
        # a new password still goes through the policy; see
        # ``identity.provisioning``.
        self._credentials.set_credential(
            PasswordCredential(
                user_id=user.user_id,
                password_hash=self._hasher.hash(password),
                created_at_utc=credential.created_at_utc,
                updated_at_utc=utc_timestamp(self._clock),
            )
        )
