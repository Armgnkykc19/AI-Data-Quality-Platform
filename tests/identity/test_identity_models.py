"""The identity domain: closed enums, validated identifiers, no credentials.

These models are what Sprint 13 will eventually authenticate and authorize
against, so the properties tested here are the ones a later phase depends on
being true without re-checking: a role vocabulary that cannot quietly grow, an
identifier that cannot be derived from anything mutable, and a login key that
the database and a lookup will agree on.
"""

from __future__ import annotations

import dataclasses

import pytest

from identity.errors import IdentityValidationError
from identity.ids import (
    MEMBERSHIP_ID_PREFIX,
    ORGANIZATION_ID_PREFIX,
    USER_ID_PREFIX,
    assert_opaque_id,
    generate_opaque_id,
    new_membership_id,
    new_organization_id,
    new_user_id,
)
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
    UserStatus,
    normalize_organization_slug,
)

NOW = "2026-09-20T09:00:00Z"
ORGANIZATION_ID = "ORG-test-a"
USER_ID = "USR-test-a"


def make_user(**overrides: object) -> User:
    kwargs: dict[str, object] = {
        "email": "Ada@Example.com",
        "display_name": "Ada",
        "created_at_utc": NOW,
        "user_id": USER_ID,
    }
    kwargs.update(overrides)
    return User.create(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_exactly_two_membership_roles_exist() -> None:
    """The vocabulary is closed at two, because the product has two capability sets.

    A reviewer may record a decision and a viewer may not. Every other
    distinction in this system -- who creates a tenant, who registers a queue --
    is an operator action performed through the CLI, so a role for it would
    gate nothing and could never be tested.
    """
    assert {role.value for role in MembershipRole} == {"REVIEWER", "VIEWER"}


@pytest.mark.parametrize("token", ["OWNER", "ADMIN", "owner", "reviewer", "", "*"])
def test_no_other_role_token_is_accepted(token: str) -> None:
    with pytest.raises(ValueError):
        MembershipRole(token)


def test_a_membership_refuses_a_role_that_is_not_the_enum() -> None:
    with pytest.raises(IdentityValidationError, match="role"):
        OrganizationMembership(
            membership_id="MEM-test",
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            role="REVIEWER",  # type: ignore[arg-type]
            created_at_utc=NOW,
        )


def test_status_vocabularies_are_closed() -> None:
    assert {status.value for status in UserStatus} == {"ACTIVE", "DISABLED"}
    assert {status.value for status in OrganizationStatus} == {"ACTIVE", "SUSPENDED"}


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [
        (new_user_id, USER_ID_PREFIX),
        (new_organization_id, ORGANIZATION_ID_PREFIX),
        (new_membership_id, MEMBERSHIP_ID_PREFIX),
    ],
)
def test_generated_ids_carry_their_prefix_and_are_not_repeated(factory, prefix: str) -> None:
    minted = {factory() for _ in range(200)}

    assert len(minted) == 200
    assert all(value.startswith(prefix) for value in minted)


def test_generated_ids_are_long_enough_to_be_unguessable() -> None:
    """128 random bits. Short enough to read, long enough that seeing one
    identifier says nothing about any other."""
    value = generate_opaque_id("ORG-")

    assert len(value) - len("ORG-") == 32


@pytest.mark.parametrize(
    "value",
    ["", "USR", "USR-", "ORG-abc", "usr-abc", " USR-abc", "USR-abc ", "USR-a b"],
)
def test_malformed_identifiers_are_refused(value: str) -> None:
    with pytest.raises(IdentityValidationError):
        assert_opaque_id(value, prefix=USER_ID_PREFIX, field_name="user_id")


def test_an_over_long_identifier_is_refused() -> None:
    with pytest.raises(IdentityValidationError, match="characters"):
        assert_opaque_id("USR-" + "a" * 200, prefix=USER_ID_PREFIX, field_name="user_id")


def test_an_identifier_is_not_derived_from_anything_mutable() -> None:
    """Two users with the same address and name get different identifiers.

    A user id is the durable audit identity of every decision that user
    records. Deriving it from an address would publish that address to anyone
    who saw the id, and would change it when the address changed -- which is
    exactly the immutability durable audit attribution depends on.
    """
    first = make_user(user_id=None)
    second = make_user(user_id=None)

    assert first.user_id != second.user_id
    assert "example.com" not in first.user_id
    assert "ada" not in first.user_id.casefold()[len(USER_ID_PREFIX) :]


# --------------------------------------------------------------------------
# User
# --------------------------------------------------------------------------


def test_a_user_keeps_the_entered_address_and_derives_the_lookup_key() -> None:
    user = make_user(email="  Ada@Example.COM  ")

    assert user.email == "Ada@Example.COM"
    assert user.normalized_email == "ada@example.com"


def test_a_user_whose_lookup_key_disagrees_with_its_address_is_refused() -> None:
    """The stored key must be the normalization of the stored address.

    Otherwise the UNIQUE constraint on normalized_email stops describing what a
    lookup will actually find, and two rows for one person become possible.
    """
    with pytest.raises(IdentityValidationError, match="normalized_email"):
        User(
            user_id=USER_ID,
            email="Ada@Example.com",
            normalized_email="someone.else@example.com",
            display_name="Ada",
            status=UserStatus.ACTIVE,
            created_at_utc=NOW,
        )


def test_a_user_carries_no_credential_field() -> None:
    """Identity metadata and credential material are separate on purpose.

    They have different lifetimes and different access rules, and a placeholder
    column filled with a lie until the authentication phase would be worse than
    an absent one.
    """
    fields = set(User.__dataclass_fields__)

    assert not {name for name in fields if "password" in name or "secret" in name}


@pytest.mark.parametrize("value", ["", "   ", "no-at-sign", "a@b", "two@@example.com", "a b@c.com"])
def test_a_user_refuses_a_login_handle_that_is_not_an_address(value: str) -> None:
    with pytest.raises(IdentityValidationError):
        make_user(email=value)


def test_a_user_refuses_an_empty_display_name() -> None:
    with pytest.raises(IdentityValidationError, match="display_name"):
        make_user(display_name="   ")


# --------------------------------------------------------------------------
# Organization
# --------------------------------------------------------------------------


def test_an_organization_slug_is_casefolded_on_construction() -> None:
    """So the UNIQUE constraint and a slug lookup agree on what one tenant is."""
    organization = Organization.create(
        slug="Acme-TR",
        display_name="Acme",
        created_at_utc=NOW,
        organization_id=ORGANIZATION_ID,
    )

    assert organization.slug == "acme-tr"
    assert organization.display_name == "Acme"


@pytest.mark.parametrize(
    "slug",
    ["", "   ", "acme tr", "acme.tr", "acme/tr", "-acme", "acme-", "_acme", "acme!", "a" * 65],
)
def test_a_malformed_slug_is_refused(slug: str) -> None:
    with pytest.raises(IdentityValidationError):
        normalize_organization_slug(slug)


def test_a_non_string_slug_is_refused() -> None:
    with pytest.raises(IdentityValidationError, match="string"):
        normalize_organization_slug(7)


def test_an_organization_refuses_a_status_that_is_not_the_enum() -> None:
    with pytest.raises(IdentityValidationError, match="status"):
        Organization(
            organization_id=ORGANIZATION_ID,
            slug="acme",
            display_name="Acme",
            status="ACTIVE",  # type: ignore[arg-type]
            created_at_utc=NOW,
        )


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


def test_a_membership_validates_both_sides_of_the_join() -> None:
    with pytest.raises(IdentityValidationError, match="organization_id"):
        OrganizationMembership.create(
            organization_id="not-an-org-id",
            user_id=USER_ID,
            role=MembershipRole.REVIEWER,
            created_at_utc=NOW,
        )

    with pytest.raises(IdentityValidationError, match="user_id"):
        OrganizationMembership.create(
            organization_id=ORGANIZATION_ID,
            user_id="ORG-wrong-prefix",
            role=MembershipRole.VIEWER,
            created_at_utc=NOW,
        )


def test_identity_models_are_immutable() -> None:
    """Frozen, so a stored object cannot be edited into disagreeing with its row."""
    user = make_user()

    with pytest.raises(dataclasses.FrozenInstanceError):
        user.display_name = "Someone else"  # type: ignore[misc]
