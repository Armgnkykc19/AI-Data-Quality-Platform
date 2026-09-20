"""Storage for the tenant graph: organizations, users, memberships, queues.

Every constraint here is enforced by the database, not by Python, and these
tests execute the real DDL to prove it. That distinction matters because the
whole tenancy design rests on foreign keys: if ``review_queues.organization_id``
were a convention rather than a constraint, review data could end up owned by
an organization that does not exist, and nothing would notice.
"""

from __future__ import annotations

import pytest

from identity.errors import DuplicateIdentityError, IdentityNotFoundError
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
    UserStatus,
)
from review_application.queues import ReviewQueue
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository

NOW = "2026-09-20T09:00:00Z"


@pytest.fixture
def tenants(database: ReviewDatabase) -> SqliteTenantRepository:
    return SqliteTenantRepository(database)


def make_organization(slug: str = "acme", organization_id: str = "ORG-acme") -> Organization:
    return Organization.create(
        slug=slug,
        display_name=slug.upper(),
        created_at_utc=NOW,
        organization_id=organization_id,
    )


def make_user(email: str = "ada@example.com", user_id: str = "USR-ada") -> User:
    return User.create(
        email=email,
        display_name="Ada",
        created_at_utc=NOW,
        user_id=user_id,
    )


# --------------------------------------------------------------------------
# Organizations
# --------------------------------------------------------------------------


def test_an_organization_round_trips_by_id_and_by_slug(tenants: SqliteTenantRepository) -> None:
    stored = tenants.create_organization(make_organization())

    assert tenants.get_organization(stored.organization_id) == stored
    assert tenants.get_organization_by_slug("acme") == stored


def test_a_slug_lookup_normalizes_the_way_the_write_did(
    tenants: SqliteTenantRepository,
) -> None:
    """An operator typing ``Acme`` must find the tenant stored as ``acme``.

    If the lookup and the UNIQUE constraint disagreed on what one slug is, the
    second ``create-organization`` would succeed and produce a second tenant
    for the same name.
    """
    tenants.create_organization(make_organization(slug="Acme"))

    assert tenants.get_organization_by_slug("ACME") is not None
    assert tenants.get_organization_by_slug("  acme  ") is not None


def test_a_duplicate_organization_id_is_refused(tenants: SqliteTenantRepository) -> None:
    tenants.create_organization(make_organization())

    with pytest.raises(DuplicateIdentityError):
        tenants.create_organization(make_organization(slug="other"))


def test_a_duplicate_slug_is_refused(tenants: SqliteTenantRepository) -> None:
    tenants.create_organization(make_organization())

    with pytest.raises(DuplicateIdentityError):
        tenants.create_organization(make_organization(organization_id="ORG-second"))


def test_a_case_variant_slug_is_the_same_tenant(tenants: SqliteTenantRepository) -> None:
    tenants.create_organization(make_organization(slug="acme"))

    with pytest.raises(DuplicateIdentityError):
        tenants.create_organization(make_organization(slug="ACME", organization_id="ORG-second"))


def test_an_unknown_organization_reads_as_none(tenants: SqliteTenantRepository) -> None:
    assert tenants.get_organization("ORG-never-created") is None
    assert tenants.get_organization_by_slug("never-created") is None


def test_organizations_are_listed_for_operator_tooling(
    tenants: SqliteTenantRepository,
) -> None:
    tenants.create_organization(make_organization(slug="beta", organization_id="ORG-beta"))
    tenants.create_organization(make_organization(slug="acme", organization_id="ORG-acme"))

    assert [item.slug for item in tenants.list_organizations()] == ["acme", "beta"]


def test_a_suspended_organization_round_trips_its_status(
    tenants: SqliteTenantRepository,
) -> None:
    stored = tenants.create_organization(
        Organization.create(
            slug="acme",
            display_name="Acme",
            created_at_utc=NOW,
            organization_id="ORG-acme",
            status=OrganizationStatus.SUSPENDED,
        )
    )

    assert tenants.get_organization(stored.organization_id).status is OrganizationStatus.SUSPENDED


def test_the_schema_refuses_an_unknown_organization_status(
    database: ReviewDatabase,
) -> None:
    """The CHECK is derived from the enum, so it cannot drift away from it."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            "INSERT INTO organizations (organization_id, slug, display_name, status, "
            "created_at_utc) VALUES ('ORG-x', 'x', 'X', 'DELETED', ?)",
            (NOW,),
        )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def test_a_user_round_trips_with_both_address_forms(tenants: SqliteTenantRepository) -> None:
    stored = tenants.create_user(make_user(email="Ada@Example.com"))

    reloaded = tenants.get_user(stored.user_id)

    assert reloaded == stored
    assert reloaded.email == "Ada@Example.com"
    assert reloaded.normalized_email == "ada@example.com"


def test_a_user_is_found_by_any_case_variant_of_its_address(
    tenants: SqliteTenantRepository,
) -> None:
    stored = tenants.create_user(make_user(email="Ada@Example.com"))

    assert tenants.get_user_by_email("ADA@EXAMPLE.COM") == stored
    assert tenants.get_user_by_email("  ada@example.com ") == stored


def test_a_duplicate_user_id_is_refused(tenants: SqliteTenantRepository) -> None:
    tenants.create_user(make_user())

    with pytest.raises(DuplicateIdentityError):
        tenants.create_user(make_user(email="other@example.com"))


def test_a_duplicate_login_handle_is_refused(tenants: SqliteTenantRepository) -> None:
    """Two accounts for one mailbox would make a later login ambiguous."""
    tenants.create_user(make_user(email="ada@example.com"))

    with pytest.raises(DuplicateIdentityError):
        tenants.create_user(make_user(email="ADA@Example.com", user_id="USR-second"))


def test_a_duplicate_user_error_does_not_echo_the_address(
    tenants: SqliteTenantRepository,
) -> None:
    """The message must not become a membership oracle once this path is reachable."""
    tenants.create_user(make_user(email="ada@example.com"))

    with pytest.raises(DuplicateIdentityError) as failure:
        tenants.create_user(make_user(email="ada@example.com", user_id="USR-second"))

    assert "ada@example.com" not in str(failure.value)


def test_a_disabled_user_round_trips_its_status(tenants: SqliteTenantRepository) -> None:
    stored = tenants.create_user(
        User.create(
            email="ada@example.com",
            display_name="Ada",
            created_at_utc=NOW,
            user_id="USR-ada",
            status=UserStatus.DISABLED,
        )
    )

    assert tenants.get_user(stored.user_id).status is UserStatus.DISABLED


def test_an_unknown_user_reads_as_none(tenants: SqliteTenantRepository) -> None:
    assert tenants.get_user("USR-never-created") is None
    assert tenants.get_user_by_email("nobody@example.com") is None


# --------------------------------------------------------------------------
# Memberships
# --------------------------------------------------------------------------


def seed_pair(tenants: SqliteTenantRepository) -> tuple[Organization, User]:
    return tenants.create_organization(make_organization()), tenants.create_user(make_user())


def make_membership(
    organization: Organization,
    user: User,
    *,
    role: MembershipRole = MembershipRole.REVIEWER,
    membership_id: str = "MEM-one",
) -> OrganizationMembership:
    return OrganizationMembership.create(
        organization_id=organization.organization_id,
        user_id=user.user_id,
        role=role,
        created_at_utc=NOW,
        membership_id=membership_id,
    )


def test_a_membership_round_trips(tenants: SqliteTenantRepository) -> None:
    organization, user = seed_pair(tenants)

    stored = tenants.create_membership(make_membership(organization, user))

    assert (
        tenants.get_membership(organization_id=organization.organization_id, user_id=user.user_id)
        == stored
    )


@pytest.mark.parametrize("role", list(MembershipRole))
def test_every_role_in_the_enum_is_storable(
    tenants: SqliteTenantRepository, role: MembershipRole
) -> None:
    organization, user = seed_pair(tenants)

    stored = tenants.create_membership(make_membership(organization, user, role=role))

    assert stored.role is role


def test_the_schema_refuses_a_role_outside_the_enum(database: ReviewDatabase) -> None:
    import sqlite3

    tenants = SqliteTenantRepository(database)
    organization, user = seed_pair(tenants)

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            "INSERT INTO organization_memberships (membership_id, organization_id, user_id, "
            "role, created_at_utc) VALUES ('MEM-x', ?, ?, 'ADMIN', ?)",
            (organization.organization_id, user.user_id, NOW),
        )


def test_a_second_membership_for_one_pair_is_refused(tenants: SqliteTenantRepository) -> None:
    """One role per person per tenant, so a capability check never has to choose."""
    organization, user = seed_pair(tenants)
    tenants.create_membership(make_membership(organization, user))

    with pytest.raises(DuplicateIdentityError):
        tenants.create_membership(
            make_membership(organization, user, role=MembershipRole.VIEWER, membership_id="MEM-two")
        )


def test_a_membership_into_an_unknown_organization_is_refused(
    tenants: SqliteTenantRepository,
) -> None:
    user = tenants.create_user(make_user())

    with pytest.raises(IdentityNotFoundError, match="Organization"):
        tenants.create_membership(
            OrganizationMembership.create(
                organization_id="ORG-never-created",
                user_id=user.user_id,
                role=MembershipRole.REVIEWER,
                created_at_utc=NOW,
                membership_id="MEM-one",
            )
        )


def test_a_membership_for_an_unknown_user_is_refused(tenants: SqliteTenantRepository) -> None:
    organization = tenants.create_organization(make_organization())

    with pytest.raises(IdentityNotFoundError, match="User"):
        tenants.create_membership(
            OrganizationMembership.create(
                organization_id=organization.organization_id,
                user_id="USR-never-created",
                role=MembershipRole.REVIEWER,
                created_at_utc=NOW,
                membership_id="MEM-one",
            )
        )


def test_a_non_member_reads_as_none(tenants: SqliteTenantRepository) -> None:
    """The same answer as an organization that does not exist, deliberately.

    A caller cannot tell the two apart, which is exactly what a non-member
    should be able to learn about a tenant: nothing.
    """
    organization, user = seed_pair(tenants)

    assert (
        tenants.get_membership(organization_id=organization.organization_id, user_id=user.user_id)
        is None
    )
    assert tenants.get_membership(organization_id="ORG-never-created", user_id=user.user_id) is None


def test_a_user_can_belong_to_several_organizations(tenants: SqliteTenantRepository) -> None:
    user = tenants.create_user(make_user())
    first = tenants.create_organization(make_organization(slug="acme", organization_id="ORG-a"))
    second = tenants.create_organization(make_organization(slug="beta", organization_id="ORG-b"))

    tenants.create_membership(make_membership(first, user, membership_id="MEM-a"))
    tenants.create_membership(
        make_membership(second, user, role=MembershipRole.VIEWER, membership_id="MEM-b")
    )

    memberships = tenants.list_memberships_for_user(user.user_id)

    assert {item.organization_id for item in memberships} == {"ORG-a", "ORG-b"}
    assert {item.role for item in memberships} == {MembershipRole.REVIEWER, MembershipRole.VIEWER}


# --------------------------------------------------------------------------
# Review queues
# --------------------------------------------------------------------------


def make_queue(
    organization: Organization,
    *,
    name: str = "default",
    review_queue_id: str = "RQ-one",
) -> ReviewQueue:
    return ReviewQueue.create(
        organization_id=organization.organization_id,
        name=name,
        created_at_utc=NOW,
        review_queue_id=review_queue_id,
    )


def test_a_queue_round_trips_by_id_and_by_name(tenants: SqliteTenantRepository) -> None:
    organization = tenants.create_organization(make_organization())

    stored = tenants.create_review_queue(make_queue(organization))

    assert tenants.get_review_queue(stored.review_queue_id) == stored
    assert (
        tenants.get_review_queue_by_name(
            organization_id=organization.organization_id, name="default"
        )
        == stored
    )


def test_a_queue_into_an_unknown_organization_is_refused(
    tenants: SqliteTenantRepository,
) -> None:
    """A queue is not a path through which a tenant comes into existence."""
    with pytest.raises(IdentityNotFoundError, match="Organization"):
        tenants.create_review_queue(
            ReviewQueue.create(
                organization_id="ORG-never-created",
                name="default",
                created_at_utc=NOW,
                review_queue_id="RQ-one",
            )
        )


def test_a_duplicate_queue_id_is_refused(tenants: SqliteTenantRepository) -> None:
    organization = tenants.create_organization(make_organization())
    tenants.create_review_queue(make_queue(organization))

    with pytest.raises(DuplicateIdentityError):
        tenants.create_review_queue(make_queue(organization, name="other"))


def test_a_duplicate_queue_name_within_one_organization_is_refused(
    tenants: SqliteTenantRepository,
) -> None:
    organization = tenants.create_organization(make_organization())
    tenants.create_review_queue(make_queue(organization))

    with pytest.raises(DuplicateIdentityError):
        tenants.create_review_queue(make_queue(organization, review_queue_id="RQ-two"))


def test_two_organizations_may_use_the_same_queue_name(
    tenants: SqliteTenantRepository,
) -> None:
    """Queue names are scoped to a tenant, so "default" is not a global name."""
    first = tenants.create_organization(make_organization(slug="acme", organization_id="ORG-a"))
    second = tenants.create_organization(make_organization(slug="beta", organization_id="ORG-b"))

    tenants.create_review_queue(make_queue(first, review_queue_id="RQ-a"))
    tenants.create_review_queue(make_queue(second, review_queue_id="RQ-b"))

    assert (
        tenants.get_review_queue_by_name(organization_id="ORG-a", name="default").review_queue_id
        == "RQ-a"
    )
    assert (
        tenants.get_review_queue_by_name(organization_id="ORG-b", name="default").review_queue_id
        == "RQ-b"
    )


def test_one_organization_may_own_several_queues(tenants: SqliteTenantRepository) -> None:
    """The schema supports it even though the product exposes one for now.

    A UNIQUE constraint on organization_id alone would have encoded a product
    default into the schema, where relaxing it later would be a migration
    rather than a decision.
    """
    organization = tenants.create_organization(make_organization())
    tenants.create_review_queue(make_queue(organization, name="first", review_queue_id="RQ-a"))
    tenants.create_review_queue(make_queue(organization, name="second", review_queue_id="RQ-b"))

    queues = tenants.list_review_queues(organization.organization_id)

    assert {item.name for item in queues} == {"first", "second"}


def test_listing_queues_is_scoped_to_one_organization(
    tenants: SqliteTenantRepository,
) -> None:
    first = tenants.create_organization(make_organization(slug="acme", organization_id="ORG-a"))
    second = tenants.create_organization(make_organization(slug="beta", organization_id="ORG-b"))
    tenants.create_review_queue(make_queue(first, review_queue_id="RQ-a"))
    tenants.create_review_queue(make_queue(second, review_queue_id="RQ-b"))

    assert [item.review_queue_id for item in tenants.list_review_queues("ORG-a")] == ["RQ-a"]
    assert [item.review_queue_id for item in tenants.list_review_queues("ORG-b")] == ["RQ-b"]
    # The unscoped read exists for operator tooling only, and sees both.
    assert len(tenants.list_all_review_queues()) == 2


# --------------------------------------------------------------------------
# Nothing is deletable
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [name for name in dir(SqliteTenantRepository) if not name.startswith("_")],
)
def test_the_tenant_repository_exposes_no_delete_primitive(name: str) -> None:
    """A user id is the durable audit identity of every decision it recorded.

    ON DELETE RESTRICT guards the references, but the stronger guarantee is
    that no method here can even ask.
    """
    assert "delete" not in name
    assert "remove" not in name
    assert "drop" not in name


def test_an_organization_owning_a_queue_cannot_be_deleted(
    database: ReviewDatabase,
    tenants: SqliteTenantRepository,
) -> None:
    import sqlite3

    organization = tenants.create_organization(make_organization())
    tenants.create_review_queue(make_queue(organization))

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            "DELETE FROM organizations WHERE organization_id = ?",
            (organization.organization_id,),
        )


def test_a_user_named_by_a_membership_cannot_be_deleted(
    database: ReviewDatabase,
    tenants: SqliteTenantRepository,
) -> None:
    import sqlite3

    organization, user = seed_pair(tenants)
    tenants.create_membership(make_membership(organization, user))

    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute("DELETE FROM users WHERE user_id = ?", (user.user_id,))
