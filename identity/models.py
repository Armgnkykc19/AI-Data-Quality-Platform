"""Users, organizations, and the membership that joins them.

Three frozen dataclasses, validated on construction, with no framework in
sight: no Pydantic, no FastAPI, no ``sqlite3`` row type, no React. They are the
same shape as ``human_review.models`` for the same reason -- a domain object
that knows how it is transported or stored cannot be reused by a second
transport or a second store, and cannot be constructed in a test without one.

What is deliberately absent:

* **No credential material.** There is no ``password_hash`` field and no
  placeholder standing in for one. Identity metadata and credential material
  have different lifetimes, different access rules, and different reasons to
  change; binding them into one object would mean every read of a display name
  carried a secret. Credentials arrive in the authentication phase, in their
  own table, keyed by ``user_id``.
* **No role hierarchy and no permission rows.** ``MembershipRole`` is a closed
  two-value enum. A role table, a wildcard grant, or a role-inherits-role edge
  would be machinery for capabilities this product does not have.
* **No tenant knowledge in the Sprint 08 domain.** Nothing in ``human_review``
  imports this module, and nothing here imports ``human_review``. Whether a
  MATCH is safe is a question about review evidence; who is asking is a
  question about access. Keeping the two packages unaware of each other is what
  stops the second from ever being able to answer the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from identity.errors import IdentityValidationError
from identity.ids import (
    MEMBERSHIP_ID_PREFIX,
    ORGANIZATION_ID_PREFIX,
    USER_ID_PREFIX,
    assert_opaque_id,
    new_membership_id,
    new_organization_id,
    new_user_id,
)
from identity.normalization import MAX_EMAIL_LENGTH, normalize_login_email

__all__ = [
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "MembershipRole",
    "Organization",
    "OrganizationMembership",
    "OrganizationStatus",
    "User",
    "UserStatus",
    "normalize_organization_slug",
]

MAX_DISPLAY_NAME_LENGTH = 200
MAX_SLUG_LENGTH = 64
MAX_TIMESTAMP_LENGTH = 64

# Deliberately narrow: lowercase ASCII letters, digits, and two separators. A
# slug is operator ergonomics -- it is what a CLI argument names -- so it must
# be typeable, unambiguous, and free of anything a shell would interpret. It is
# never a security boundary and never an identifier: every reference between
# tables uses the opaque organization_id.
_SLUG_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")
_SLUG_SEPARATORS = "-_"


class MembershipRole(StrEnum):
    """What a member of an organization may do with its review queues.

    Exactly two values, because the product has exactly two capability sets: a
    reviewer may record a decision and a viewer may not. Every other
    distinction that could be drawn -- who manages membership, who registers a
    queue -- is an operator action performed through the CLI, not an action any
    HTTP principal performs, so a role for it would gate nothing.
    """

    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class UserStatus(StrEnum):
    """Whether this user may eventually hold an authenticated session.

    Stored rather than inferred from the absence of memberships: a user removed
    from every organization is still a user whose decisions are in the audit
    trail, and a disabled user must stay referenceable for exactly that reason.
    Deletion is not the mechanism; ON DELETE RESTRICT enforces that.
    """

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class OrganizationStatus(StrEnum):
    """Whether this organization's queues may be worked on."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


def _require_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise IdentityValidationError(f"{field_name} must be a string.")
    stripped = value.strip()
    if not stripped:
        raise IdentityValidationError(f"{field_name} must not be empty.")
    if len(stripped) > max_length:
        raise IdentityValidationError(f"{field_name} exceeds {max_length} characters.")
    return stripped


def normalize_organization_slug(value: object) -> str:
    """Casefold and validate an operator-facing organization slug.

    Normalized the same way on write and on lookup, so the UNIQUE constraint
    and ``get_organization_by_slug`` agree on what counts as the same slug --
    the same discipline ``normalize_login_email`` applies to a login handle.
    """
    if not isinstance(value, str):
        raise IdentityValidationError(f"slug must be a string; got {type(value).__name__}.")
    candidate = value.strip().casefold()
    if not candidate:
        raise IdentityValidationError("slug must not be empty.")
    if len(candidate) > MAX_SLUG_LENGTH:
        raise IdentityValidationError(f"slug exceeds {MAX_SLUG_LENGTH} characters.")
    unexpected = sorted(set(candidate) - _SLUG_ALPHABET)
    if unexpected:
        raise IdentityValidationError(
            f"slug may contain only lowercase letters, digits, '-' and '_'; got {unexpected}."
        )
    if candidate[0] in _SLUG_SEPARATORS or candidate[-1] in _SLUG_SEPARATORS:
        raise IdentityValidationError("slug must not start or end with a separator.")
    return candidate


@dataclass(frozen=True)
class Organization:
    """A tenant. Owns review queues; owns no review case directly."""

    organization_id: str
    slug: str
    display_name: str
    status: OrganizationStatus
    created_at_utc: str

    def __post_init__(self) -> None:
        assert_opaque_id(
            self.organization_id,
            prefix=ORGANIZATION_ID_PREFIX,
            field_name="organization_id",
        )
        object.__setattr__(self, "slug", normalize_organization_slug(self.slug))
        object.__setattr__(
            self,
            "display_name",
            _require_text(self.display_name, "display_name", max_length=MAX_DISPLAY_NAME_LENGTH),
        )
        if not isinstance(self.status, OrganizationStatus):
            raise IdentityValidationError("status must be an OrganizationStatus.")
        _require_text(self.created_at_utc, "created_at_utc", max_length=MAX_TIMESTAMP_LENGTH)

    @classmethod
    def create(
        cls,
        *,
        slug: str,
        display_name: str,
        created_at_utc: str,
        organization_id: str | None = None,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
    ) -> Organization:
        """Build a new organization, minting an id unless one is supplied.

        ``organization_id`` is a parameter rather than always generated so a
        test can pin it and assert on a stable value. Production callers omit
        it; nothing outside this process supplies one.
        """
        return cls(
            organization_id=organization_id or new_organization_id(),
            slug=slug,
            display_name=display_name,
            status=status,
            created_at_utc=created_at_utc,
        )


@dataclass(frozen=True)
class User:
    """A person who may eventually hold a session and record decisions.

    ``user_id`` is the durable audit identity: it never changes, so a decision
    recorded today still names the same person after a rename or an address
    change. ``email`` and ``display_name`` are mutable presentation values and
    are never what an audit row stores.
    """

    user_id: str
    email: str
    normalized_email: str
    display_name: str
    status: UserStatus
    created_at_utc: str

    def __post_init__(self) -> None:
        assert_opaque_id(self.user_id, prefix=USER_ID_PREFIX, field_name="user_id")
        entered = _require_text(self.email, "email", max_length=MAX_EMAIL_LENGTH)
        object.__setattr__(self, "email", entered)
        # Recomputed rather than trusted. A stored lookup key that disagrees
        # with its own address is how a UNIQUE constraint stops describing what
        # a lookup will actually find.
        if self.normalized_email != normalize_login_email(entered):
            raise IdentityValidationError(
                "normalized_email does not match the normalization of email."
            )
        object.__setattr__(
            self,
            "display_name",
            _require_text(self.display_name, "display_name", max_length=MAX_DISPLAY_NAME_LENGTH),
        )
        if not isinstance(self.status, UserStatus):
            raise IdentityValidationError("status must be a UserStatus.")
        _require_text(self.created_at_utc, "created_at_utc", max_length=MAX_TIMESTAMP_LENGTH)

    @classmethod
    def create(
        cls,
        *,
        email: str,
        display_name: str,
        created_at_utc: str,
        user_id: str | None = None,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> User:
        """Build a new user, deriving the lookup key from the entered address."""
        return cls(
            user_id=user_id or new_user_id(),
            email=email,
            normalized_email=normalize_login_email(email),
            display_name=display_name,
            status=status,
            created_at_utc=created_at_utc,
        )


@dataclass(frozen=True)
class OrganizationMembership:
    """One user's role in one organization.

    The membership, not the user, is what makes an organization's data
    reachable. A user with no membership in an organization is, for every
    purpose in this system, someone for whom that organization does not exist.
    """

    membership_id: str
    organization_id: str
    user_id: str
    role: MembershipRole
    created_at_utc: str

    def __post_init__(self) -> None:
        assert_opaque_id(
            self.membership_id, prefix=MEMBERSHIP_ID_PREFIX, field_name="membership_id"
        )
        assert_opaque_id(
            self.organization_id, prefix=ORGANIZATION_ID_PREFIX, field_name="organization_id"
        )
        assert_opaque_id(self.user_id, prefix=USER_ID_PREFIX, field_name="user_id")
        if not isinstance(self.role, MembershipRole):
            raise IdentityValidationError("role must be a MembershipRole.")
        _require_text(self.created_at_utc, "created_at_utc", max_length=MAX_TIMESTAMP_LENGTH)

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        user_id: str,
        role: MembershipRole,
        created_at_utc: str,
        membership_id: str | None = None,
    ) -> OrganizationMembership:
        return cls(
            membership_id=membership_id or new_membership_id(),
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            created_at_utc=created_at_utc,
        )
