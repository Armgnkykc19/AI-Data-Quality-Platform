"""Creating a login, and setting a password. The operator side of identity.

Separate from ``AuthenticationService`` because the two do opposite things with
opposite rules. Authentication *verifies* an already-accepted secret and
deliberately does not re-apply the password policy, so a tightened minimum
never locks out someone who did nothing wrong. This service *sets* a secret,
and the policy is exactly what it is for.

Two operations, and neither is reachable over HTTP. ``UserProvisioningService``
creates an account together with its first password, atomically, so a failure
cannot leave a user who exists but cannot sign in.
``PasswordProvisioningService`` replaces the password of a user who already
exists.

There is no self-service password change, no reset token, no public
registration and no default credential. A person can log in because an operator
deliberately created them a way to -- which is the correct starting state for
an internal tool.
"""

from __future__ import annotations

from identity.clock import Clock, system_utc_now, utc_timestamp
from identity.credentials import DEFAULT_PASSWORD_POLICY, PasswordCredential, PasswordPolicy
from identity.models import User
from identity.passwords import PasswordHasher
from identity.repository import CredentialRepository, UserProvisioningRepository

__all__ = ["PasswordProvisioningService", "UserProvisioningService"]


class PasswordProvisioningService:
    """Creates or replaces one user's password credential."""

    def __init__(
        self,
        *,
        credentials: CredentialRepository,
        hasher: PasswordHasher,
        policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
        clock: Clock = system_utc_now,
    ) -> None:
        self._credentials = credentials
        self._hasher = hasher
        self._policy = policy
        self._clock = clock

    def set_password(self, user_id: str, password: str) -> PasswordCredential:
        """Hash and store a password, replacing any credential already stored.

        The policy is checked *before* hashing, so a rejected password is never
        put through the hasher and never reaches storage in any form. The
        rejection names the rule, never the value.

        ``created_at_utc`` is preserved across a replacement: it records when
        this user first gained a way to log in, which is the question an
        operator asks of it. ``updated_at_utc`` records the change.

        The repository refuses a credential for a user that is not stored, so
        this cannot leave a verifier belonging to nobody.
        """
        allowed = self._policy.assert_allowed(password)
        now = utc_timestamp(self._clock)
        existing = self._credentials.get_credential(user_id)
        return self._credentials.set_credential(
            PasswordCredential(
                user_id=user_id,
                password_hash=self._hasher.hash(allowed),
                created_at_utc=existing.created_at_utc if existing is not None else now,
                updated_at_utc=now,
            )
        )


class UserProvisioningService:
    """Creates a login-capable user: the account and its password together.

    The operator equivalent of a sign-up, except that nobody signs themselves
    up. There is no public registration, no invitation token and no default
    account; a person can log in because an operator deliberately created them
    a way to.
    """

    def __init__(
        self,
        *,
        provisioning: UserProvisioningRepository,
        hasher: PasswordHasher,
        policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
        clock: Clock = system_utc_now,
    ) -> None:
        self._provisioning = provisioning
        self._hasher = hasher
        self._policy = policy
        self._clock = clock

    def provision_user(self, *, email: str, display_name: str, password: str) -> User:
        """Create the user and their credential, or create neither.

        Order matters and is not incidental. The password policy is checked
        first, so a rejected password never reaches the hasher; the user object
        is built next, so a malformed address is refused before anything is
        hashed; and only then is the plaintext hashed and both rows written in
        one transaction by the repository.

        Nothing else is created. No organization, no membership, no role, no
        review queue. Identity provisioning and authorization provisioning are
        separate decisions, and a command that did both would mean creating an
        account implied granting it access to something.
        """
        allowed = self._policy.assert_allowed(password)
        now = utc_timestamp(self._clock)
        user = User.create(email=email, display_name=display_name, created_at_utc=now)
        credential = PasswordCredential(
            user_id=user.user_id,
            password_hash=self._hasher.hash(allowed),
            created_at_utc=now,
            updated_at_utc=now,
        )
        return self._provisioning.create_user_with_credential(user, credential)
