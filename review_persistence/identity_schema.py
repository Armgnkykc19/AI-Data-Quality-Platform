"""SQLite DDL for the tenant graph: organizations, users, memberships, queues.

These tables live in the same database file as the review tables, and that is a
decision rather than an omission. ``review_queues.organization_id`` is a real
foreign key into ``organizations``, and ``organization_memberships`` has real
foreign keys into both ``organizations`` and ``users``; split across two files,
none of those could exist and tenant ownership would be a Python convention
again. The runtime also holds exactly one ``sqlite3`` connection on one thread
(see ``review_persistence.sqlite.database``), so a second database would mean a
second connection and a second lifecycle for no gain.

Kept in its own module so the review DDL stays readable and so the ownership
boundary is visible in the file layout: this module knows nothing about review
cases, and ``review_persistence.schema`` composes the two into one schema.

``ON DELETE RESTRICT`` throughout. A user named by a durable audit row, an
organization owning a queue, a queue owning cases -- none of them may be
deleted out from under what references them. Deactivation is a status change;
deletion is not a supported operation and no repository method offers one.
"""

from __future__ import annotations

from identity.models import MembershipRole, OrganizationStatus, UserStatus

__all__ = [
    "ALLOWED_MEMBERSHIP_ROLES",
    "ALLOWED_ORGANIZATION_STATUS_VALUES",
    "ALLOWED_USER_STATUS_VALUES",
    "IDENTITY_INDEX_STATEMENTS",
    "IDENTITY_TABLES",
    "IDENTITY_TABLE_STATEMENTS",
    "ORGANIZATIONS_TABLE",
    "ORGANIZATION_MEMBERSHIPS_TABLE",
    "REVIEW_QUEUES_TABLE",
    "USERS_TABLE",
]

ORGANIZATIONS_TABLE = "organizations"
USERS_TABLE = "users"
ORGANIZATION_MEMBERSHIPS_TABLE = "organization_memberships"
REVIEW_QUEUES_TABLE = "review_queues"

IDENTITY_TABLES: tuple[str, ...] = (
    ORGANIZATIONS_TABLE,
    USERS_TABLE,
    ORGANIZATION_MEMBERSHIPS_TABLE,
    REVIEW_QUEUES_TABLE,
)

# Derived from the live enums rather than retyped, exactly as the review DDL
# derives its status and event tokens: a CHECK constraint that is a second copy
# of an enum is a CHECK constraint that will eventually disagree with it.
ALLOWED_MEMBERSHIP_ROLES = frozenset(role.value for role in MembershipRole)
ALLOWED_USER_STATUS_VALUES = frozenset(status.value for status in UserStatus)
ALLOWED_ORGANIZATION_STATUS_VALUES = frozenset(status.value for status in OrganizationStatus)


def _sql_token_list(values: frozenset[str]) -> str:
    """Render a sorted, quoted SQL IN-list so DDL text stays deterministic."""
    return ", ".join(f"'{value}'" for value in sorted(values))


# slug is UNIQUE and casefolded by the application before it ever arrives here,
# so "Acme" and "acme" cannot become two tenants. The uniqueness is on the
# normalized value for the same reason users.normalized_email is: the database
# and the lookup must agree on what counts as the same row.
CREATE_ORGANIZATIONS = f"""
CREATE TABLE IF NOT EXISTS {ORGANIZATIONS_TABLE} (
    organization_id TEXT PRIMARY KEY NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ({_sql_token_list(ALLOWED_ORGANIZATION_STATUS_VALUES)})),
    created_at_utc TEXT NOT NULL
)
""".strip()


# Two address columns, and the split is the design. ``email`` is what the
# operator entered and what is displayed back; ``normalized_email`` is the
# lookup key produced by identity.normalization.normalize_login_email and is
# the only one carrying UNIQUE. A single folded column would lose the entered
# spelling; a single raw column would let two case-variant spellings become two
# accounts for one person.
#
# No password column. Credential material has a different lifetime and a
# different access rule than identity metadata, and inventing a placeholder
# here would mean shipping a column whose only content is a lie until the
# authentication phase fills it.
CREATE_USERS = f"""
CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
    user_id TEXT PRIMARY KEY NOT NULL,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ({_sql_token_list(ALLOWED_USER_STATUS_VALUES)})),
    created_at_utc TEXT NOT NULL
)
""".strip()


# UNIQUE (organization_id, user_id): one membership per person per tenant, so a
# user can never hold two roles in one organization and a later capability
# check can never have to choose between them.
CREATE_ORGANIZATION_MEMBERSHIPS = f"""
CREATE TABLE IF NOT EXISTS {ORGANIZATION_MEMBERSHIPS_TABLE} (
    membership_id TEXT PRIMARY KEY NOT NULL,
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL
        CHECK (role IN ({_sql_token_list(ALLOWED_MEMBERSHIP_ROLES)})),
    created_at_utc TEXT NOT NULL,
    UNIQUE (organization_id, user_id),
    FOREIGN KEY (organization_id)
        REFERENCES {ORGANIZATIONS_TABLE} (organization_id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id)
        REFERENCES {USERS_TABLE} (user_id) ON DELETE RESTRICT
)
""".strip()


# UNIQUE (organization_id, name) and deliberately NOT UNIQUE (organization_id).
# An organization holds as many queues as it has workflows; each carries its own
# authorization context, and a NO_MATCH recorded in one says nothing about a
# MATCH in another. A uniqueness constraint on organization_id alone would
# encode a product default -- one queue per tenant -- into the schema, where
# relaxing it later would be a migration rather than a decision.
CREATE_REVIEW_QUEUES = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_QUEUES_TABLE} (
    review_queue_id TEXT PRIMARY KEY NOT NULL,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (organization_id, name),
    FOREIGN KEY (organization_id)
        REFERENCES {ORGANIZATIONS_TABLE} (organization_id) ON DELETE RESTRICT
)
""".strip()


IDENTITY_TABLE_STATEMENTS: tuple[str, ...] = (
    CREATE_ORGANIZATIONS,
    CREATE_USERS,
    CREATE_ORGANIZATION_MEMBERSHIPS,
    CREATE_REVIEW_QUEUES,
)


IDENTITY_INDEX_STATEMENTS: tuple[str, ...] = (
    # "Which organizations does this user belong to" is the question a future
    # session bootstrap asks on every request, so it gets an index rather than
    # a scan. The reverse direction is already covered by the UNIQUE above.
    f"CREATE INDEX IF NOT EXISTS ix_organization_memberships_user "
    f"ON {ORGANIZATION_MEMBERSHIPS_TABLE} (user_id)",
    # "Which queues does this organization own" -- the lookup that turns a
    # verified membership into a tenant scope.
    f"CREATE INDEX IF NOT EXISTS ix_review_queues_organization "
    f"ON {REVIEW_QUEUES_TABLE} (organization_id)",
)
