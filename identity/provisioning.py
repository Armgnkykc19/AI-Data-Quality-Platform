"""Setting a user's password. The operator side of the credential store.

Separate from ``AuthenticationService`` because the two do opposite things with
opposite rules. Authentication *verifies* an already-accepted secret and
deliberately does not re-apply the password policy, so a tightened minimum
never locks out someone who did nothing wrong. This service *sets* a secret,
and the policy is exactly what it is for.

There is one operation, and it is the one a later operator command will call
behind ``getpass``. It is not reachable over HTTP, there is no self-service
password change, no reset token, and no default credential -- a user exists
with no way to log in until an operator gives them one, which is the correct
starting state for an internal tool.
"""

from __future__ import annotations

from identity.clock import Clock, system_utc_now, utc_timestamp
from identity.credentials import DEFAULT_PASSWORD_POLICY, PasswordCredential, PasswordPolicy
from identity.passwords import PasswordHasher
from identity.repository import CredentialRepository

__all__ = ["PasswordProvisioningService"]


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
