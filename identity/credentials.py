"""What a password must satisfy, and the record that stores its hash.

Two things live here and nothing else: the policy a supplied password is
checked against before it is ever hashed, and the persistence record holding
the encoded hash. Neither knows how hashing works -- that is
``identity.passwords`` -- and neither knows how a login proceeds.

The policy is deliberately small. This is an operator-provisioned internal
review tool, so there is no self-service signup to defend and no user choosing
a password under time pressure. Length is the only requirement that reliably
correlates with strength; composition rules ("one uppercase, one digit, one
symbol") push people toward ``Password1!`` and are no longer recommended by
anyone who measures the outcome.

What is deliberately absent, and must stay absent until something concrete
needs it: password history, expiry, failed-login counters, lockout tables, MFA
secrets, recovery codes, reset tokens, and breached-password lookups. Each is a
table and a lifecycle, and a speculative one is a security-sensitive surface
nothing exercises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from identity.errors import IdentityValidationError
from identity.ids import USER_ID_PREFIX, assert_opaque_id
from identity.models import MAX_TIMESTAMP_LENGTH

__all__ = [
    "DEFAULT_PASSWORD_POLICY",
    "PasswordCredential",
    "PasswordPolicy",
]


@dataclass(frozen=True)
class PasswordPolicy:
    """The bounds a password must satisfy, in one place with one owner.

    Lengths are counted in characters rather than bytes. A character is what
    the person typing actually chose; counting UTF-8 bytes would quietly reward
    non-ASCII passwords and penalise nothing, and would make the minimum mean
    different things to different alphabets.
    """

    min_length: int = 12
    max_length: int = 256

    def __post_init__(self) -> None:
        if self.min_length < 1:
            raise IdentityValidationError("min_length must be positive.")
        if self.max_length < self.min_length:
            raise IdentityValidationError("max_length must be at least min_length.")

    def assert_allowed(self, password: object) -> str:
        """Return the password unchanged, or raise.

        **Unchanged is the contract.** No strip, no casefold, no Unicode
        normalization. A password is a byte sequence the person chose, and any
        transformation here is a transformation the login path would also have
        to apply, forever, identically -- including for hashes written by an
        older build that normalized differently. The one safe rule is to hash
        exactly what was supplied.

        The rejected value is never placed in the exception. An error message
        carrying a password is a password in a log file.
        """
        if not isinstance(password, str):
            raise IdentityValidationError("password must be a string.")
        if "\x00" in password:
            # A NUL would truncate the secret in any C string boundary it
            # crossed, so two different passwords could hash identically.
            raise IdentityValidationError("password must not contain a NUL character.")
        length = len(password)
        if length < self.min_length:
            raise IdentityValidationError(
                f"password must be at least {self.min_length} characters."
            )
        if length > self.max_length:
            raise IdentityValidationError(f"password must be at most {self.max_length} characters.")
        return password


DEFAULT_PASSWORD_POLICY = PasswordPolicy()


@dataclass(frozen=True)
class PasswordCredential:
    """One user's stored password verifier.

    ``user_id`` is the primary key, which is how "one active password per user"
    is expressed structurally rather than by convention. Replacing a password
    -- including the rehash an authentication may perform -- overwrites this
    row; there is no history table, because a stored history of old verifiers
    is a stored collection of things worth attacking.

    ``password_hash`` is the encoded Argon2id string the library produced. Its
    format belongs to argon2-cffi: the salt, the parameters and the digest are
    all inside it, and nothing in this project parses, splits or re-encodes it.

    ``repr=False`` on the hash is not paranoia about reversibility -- an
    Argon2id verifier is not a password. It is about where reprs end up: an
    exception rendering, a debugger transcript, a log line. A credential record
    that prints its verifier is a credential record that eventually appears
    somewhere it was never meant to.
    """

    user_id: str
    password_hash: str = field(repr=False)
    created_at_utc: str
    updated_at_utc: str

    def __post_init__(self) -> None:
        assert_opaque_id(self.user_id, prefix=USER_ID_PREFIX, field_name="user_id")
        if not isinstance(self.password_hash, str) or not self.password_hash.strip():
            raise IdentityValidationError("password_hash must be a non-empty string.")
        for name in ("created_at_utc", "updated_at_utc"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise IdentityValidationError(f"{name} must be a non-empty string.")
            if len(value) > MAX_TIMESTAMP_LENGTH:
                raise IdentityValidationError(f"{name} exceeds {MAX_TIMESTAMP_LENGTH} characters.")
