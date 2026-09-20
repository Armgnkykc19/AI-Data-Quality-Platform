"""Opaque identifiers for tenant-graph records.

Every identifier minted here is random and carries no information. That is the
whole design, and it is the opposite of ``human_review.ids``, where
``stable_review_case_id`` derives a digest from the reviewed record pair so the
same pair always names the same case. Both are correct for what they identify:
a review case *is* its pair, while a user is not its email address and an
organization is not its slug.

Deriving these from content would be actively harmful. An identifier computed
from an email address publishes that address to anyone who sees the id, changes
when the address changes -- breaking the immutability that durable audit
attribution depends on -- and lets anyone who knows the address compute the id.
The same argument applies to a slug, a display name, and a timestamp.

``secrets`` rather than ``random`` or ``uuid4``: it is the stdlib's
cryptographically strong source, and the project already prefers a stdlib
primitive over a dependency wherever one suffices. No ULID or UUID package is
added -- 128 random bits rendered as hex is exactly what a ULID's random half
would give us, without the ordering property we deliberately do not want.
"""

from __future__ import annotations

import secrets

from identity.errors import IdentityValidationError

__all__ = [
    "MEMBERSHIP_ID_PREFIX",
    "ORGANIZATION_ID_PREFIX",
    "USER_ID_PREFIX",
    "assert_opaque_id",
    "generate_opaque_id",
    "new_membership_id",
    "new_organization_id",
    "new_user_id",
]

USER_ID_PREFIX = "USR-"
ORGANIZATION_ID_PREFIX = "ORG-"
MEMBERSHIP_ID_PREFIX = "MEM-"

# 128 bits. Enough that collision is not a failure mode worth designing around,
# and enough that an identifier cannot be guessed by anyone who has seen others.
OPAQUE_ID_BYTES = 16

# A bound, so a hand-supplied identifier cannot become an unbounded string in a
# durable primary key. Generous enough for the generated form (prefix plus 32
# hex characters) and for the readable ids tests construct.
MAX_OPAQUE_ID_LENGTH = 128


def generate_opaque_id(prefix: str) -> str:
    """Mint one random identifier under the given type prefix.

    The prefix is for humans reading a log or a database row; it is not parsed
    and confers nothing. Two record types are never distinguished by prefix
    alone -- each lives in its own table with its own foreign keys.
    """
    return f"{prefix}{secrets.token_hex(OPAQUE_ID_BYTES)}"


def new_user_id() -> str:
    return generate_opaque_id(USER_ID_PREFIX)


def new_organization_id() -> str:
    return generate_opaque_id(ORGANIZATION_ID_PREFIX)


def new_membership_id() -> str:
    return generate_opaque_id(MEMBERSHIP_ID_PREFIX)


def assert_opaque_id(value: object, *, prefix: str, field_name: str) -> str:
    """Validate an identifier without constraining how it was produced.

    The suffix is deliberately not required to be 32 hex characters. Tests and
    fixtures supply readable identifiers, and a stricter rule would mean the
    stored form of a test id differed from a production one -- which is how a
    persistence test stops exercising the production path.

    What is enforced is what storage actually depends on: a non-empty string,
    under a known type prefix, with something after the prefix, no surrounding
    or embedded whitespace, and a length a primary key can carry.
    """
    if not isinstance(value, str):
        raise IdentityValidationError(f"{field_name} must be a string; got {type(value).__name__}.")
    if value != value.strip():
        raise IdentityValidationError(f"{field_name} must not have surrounding whitespace.")
    if not value.startswith(prefix):
        raise IdentityValidationError(f"{field_name} must start with {prefix!r}.")
    if len(value) <= len(prefix):
        raise IdentityValidationError(f"{field_name} carries no value after its {prefix!r} prefix.")
    if len(value) > MAX_OPAQUE_ID_LENGTH:
        raise IdentityValidationError(f"{field_name} exceeds {MAX_OPAQUE_ID_LENGTH} characters.")
    if any(character.isspace() for character in value):
        raise IdentityValidationError(f"{field_name} must not contain whitespace.")
    return value
