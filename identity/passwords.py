"""Argon2id hashing, behind the narrowest surface that can do the job.

Three operations, and the whole point is that there are only three: hash a
password, verify one against an encoded hash, and ask whether a verified hash
should be replaced because the parameters moved. Everything else about Argon2
-- the salt, the parameter encoding, the digest length, the ``$argon2id$v=19$``
string format -- belongs to argon2-cffi and is never parsed, split, rebuilt or
stored separately by this project.

That boundary is the security decision. A hand-rolled salt column, a
parameter column, a custom encoded format or a pepper would each be a place
where this code could disagree with the library about what a stored verifier
means, and the failure mode of that disagreement is "every password verifies"
or "no password verifies".

Deliberately absent: SHA-family password hashing, PBKDF2 or bcrypt fallbacks,
reversible encryption, and any pepper mechanism. There is exactly one algorithm
and exactly one place it is configured.

Test parameters are supplied through the constructor rather than by mutating a
global, so a test can hash cheaply without production defaults ever changing.
"""

from __future__ import annotations

from typing import Protocol

from argon2 import PasswordHasher as Argon2CffiHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from argon2.low_level import Type

from identity.errors import PasswordHashingError

__all__ = [
    "Argon2idPasswordHasher",
    "PasswordHasher",
]


class PasswordHasher(Protocol):
    """What the authentication service needs, and nothing more.

    A Protocol rather than a base class, matching ``ReviewCaseRepository`` and
    ``SemanticReviewProvider``: a test can supply a spy that records which
    verifications happened without inheriting anything, which is how the
    dummy-verification tests prove the enumeration defence actually runs.
    """

    def hash(self, password: str) -> str:
        """Return a new encoded Argon2id hash. Never the same string twice."""
        ...

    def verify(self, encoded_hash: str, password: str) -> bool:
        """True when the password matches. False for every failure, including
        a malformed stored hash."""
        ...

    def needs_rehash(self, encoded_hash: str) -> bool:
        """True when a verified hash was produced with weaker parameters."""
        ...


class Argon2idPasswordHasher:
    """argon2-cffi, pinned to Argon2id, with its own defaults left alone.

    ``Type.ID`` is passed explicitly even though it is the library default.
    Argon2id is the variant RFC 9106 recommends for password hashing -- it is
    the hybrid that resists both the side-channel attacks Argon2i defends and
    the GPU tradeoffs Argon2d defends -- and a security property this important
    should be stated in the code rather than inherited silently.

    Cost parameters default to ``None``, meaning "whatever argon2-cffi
    considers current". That is deliberate: the library tracks the RFC 9106
    profiles and raises its defaults over time, and pinning numbers here would
    freeze this project at whatever was current the day it was written.
    Supplying them is how tests hash cheaply; production passes nothing.
    """

    def __init__(
        self,
        *,
        time_cost: int | None = None,
        memory_cost: int | None = None,
        parallelism: int | None = None,
    ) -> None:
        overrides = {
            name: value
            for name, value in (
                ("time_cost", time_cost),
                ("memory_cost", memory_cost),
                ("parallelism", parallelism),
            )
            if value is not None
        }
        self._hasher = Argon2CffiHasher(type=Type.ID, **overrides)

    def hash(self, password: str) -> str:
        """Hash with a fresh random salt, so the same password never repeats.

        A ``HashingError`` here is an environment failure -- the backend could
        not allocate the memory the parameters ask for -- not a bad password,
        so it becomes a typed internal error rather than a silent False.
        """
        try:
            return self._hasher.hash(password)
        except HashingError as exc:
            raise PasswordHashingError("The password hasher could not produce a hash.") from exc

    def verify(self, encoded_hash: str, password: str) -> bool:
        """Verify, returning a plain bool and never raising on a mismatch.

        Three distinct library failures collapse to ``False`` on purpose:

        * ``VerifyMismatchError`` -- the password is wrong.
        * ``InvalidHashError`` -- the stored string is not an Argon2 hash.
        * ``VerificationError`` -- verification failed for another reason.

        The second is a corrupt or hand-edited row, and the safe reading of a
        stored verifier nobody can parse is "this does not authenticate
        anyone", never "let them in". Collapsing them also keeps the library's
        exception text -- which names the algorithm and can quote the stored
        string -- from reaching a caller that might render it.
        """
        try:
            return bool(self._hasher.verify(encoded_hash, password))
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        """True when this hash predates the current parameters.

        A hash string this cannot parse is reported as *not* needing a rehash.
        It never verified anything in the first place -- ``verify`` returns
        False for it -- so there is no successful authentication to replace it
        during, and answering True would invite a caller to overwrite a row
        based on a value it failed to understand.
        """
        try:
            return bool(self._hasher.check_needs_rehash(encoded_hash))
        except (InvalidHashError, VerificationError):
            return False
