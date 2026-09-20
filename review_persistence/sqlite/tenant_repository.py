"""SQLite storage for the tenant graph: organizations, users, memberships, queues.

Deliberately not a CRUD layer. Every method here exists because a named future
consumer needs it, and the ones an ORM would generate for symmetry are absent:

* **No delete of any kind.** Not an organization, not a user, not a membership,
  not a queue. ``ON DELETE RESTRICT`` guards the references, but the stronger
  guarantee is that this module offers no primitive that could ask. A user id
  is the durable audit identity of every decision that user recorded; removing
  the row would leave history naming something that no longer exists.
  Deactivation is a status change, and it is what "removing" someone means.
* **No update.** Renames and status changes will arrive with the operator
  tooling that needs them, written as explicit single-purpose methods.
* **No generic filter or query builder.** Each read answers one question that
  something actually asks.
* **No role management surface.** A membership's role is set when the
  membership is created, by an operator.

Writes go through ``ReviewDatabase.transaction()``, which is ``BEGIN
IMMEDIATE``: a read-then-insert cannot interleave with another writer between
its two statements, so the existence checks below are decisions rather than
guesses. Referenced rows are checked explicitly before the insert so a missing
organization is reported as a missing organization, not as a constraint name --
the foreign keys remain the actual enforcement, and these checks are the error
message.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from identity.errors import DuplicateIdentityError, IdentityNotFoundError
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
    UserStatus,
    normalize_organization_slug,
)
from identity.normalization import normalize_login_email
from review_application.queues import ReviewQueue
from review_persistence.identity_schema import (
    ORGANIZATION_MEMBERSHIPS_TABLE,
    ORGANIZATIONS_TABLE,
    REVIEW_QUEUES_TABLE,
    USERS_TABLE,
)
from review_persistence.sqlite.database import Clock, ReviewDatabase, _now, utc_timestamp

__all__ = ["SqliteTenantRepository"]

_ORGANIZATION_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "slug",
    "display_name",
    "status",
    "created_at_utc",
)
_USER_COLUMNS: tuple[str, ...] = (
    "user_id",
    "email",
    "normalized_email",
    "display_name",
    "status",
    "created_at_utc",
)
_MEMBERSHIP_COLUMNS: tuple[str, ...] = (
    "membership_id",
    "organization_id",
    "user_id",
    "role",
    "created_at_utc",
)
_QUEUE_COLUMNS: tuple[str, ...] = (
    "review_queue_id",
    "organization_id",
    "name",
    "created_at_utc",
)


def _insert(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"


def _select(table: str, columns: tuple[str, ...]) -> str:
    return f"SELECT {', '.join(columns)} FROM {table}"


def _organization_from_row(row: Mapping[str, Any]) -> Organization:
    return Organization(
        organization_id=str(row["organization_id"]),
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        status=OrganizationStatus(str(row["status"])),
        created_at_utc=str(row["created_at_utc"]),
    )


def _user_from_row(row: Mapping[str, Any]) -> User:
    return User(
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        normalized_email=str(row["normalized_email"]),
        display_name=str(row["display_name"]),
        status=UserStatus(str(row["status"])),
        created_at_utc=str(row["created_at_utc"]),
    )


def _membership_from_row(row: Mapping[str, Any]) -> OrganizationMembership:
    return OrganizationMembership(
        membership_id=str(row["membership_id"]),
        organization_id=str(row["organization_id"]),
        user_id=str(row["user_id"]),
        role=MembershipRole(str(row["role"])),
        created_at_utc=str(row["created_at_utc"]),
    )


def _queue_from_row(row: Mapping[str, Any]) -> ReviewQueue:
    return ReviewQueue(
        review_queue_id=str(row["review_queue_id"]),
        organization_id=str(row["organization_id"]),
        name=str(row["name"]),
        created_at_utc=str(row["created_at_utc"]),
    )


class SqliteTenantRepository:
    """Reads and writes the tenant graph in one review database."""

    def __init__(self, database: ReviewDatabase, *, clock: Clock = _now) -> None:
        self._database = database
        self._clock = clock

    # -- organizations ------------------------------------------------------

    def create_organization(
        self,
        organization: Organization,
        *,
        now_utc: str | None = None,
    ) -> Organization:
        """Store one organization. Never upserts.

        ``now_utc`` overrides the organization's own timestamp only when the
        caller built the object without one to hand; the stored row is what is
        returned, so a caller never has to guess what was written.
        """
        stored = (
            organization
            if now_utc is None
            else Organization(
                organization_id=organization.organization_id,
                slug=organization.slug,
                display_name=organization.display_name,
                status=organization.status,
                created_at_utc=now_utc,
            )
        )
        with self._database.transaction() as connection:
            try:
                connection.execute(
                    _insert(ORGANIZATIONS_TABLE, _ORGANIZATION_COLUMNS),
                    (
                        stored.organization_id,
                        stored.slug,
                        stored.display_name,
                        stored.status.value,
                        stored.created_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateIdentityError(
                    f"An organization with id {stored.organization_id!r} or slug "
                    f"{stored.slug!r} already exists."
                ) from exc
        return stored

    def get_organization(self, organization_id: str) -> Organization | None:
        row = (
            self._database.connect()
            .execute(
                _select(ORGANIZATIONS_TABLE, _ORGANIZATION_COLUMNS) + " WHERE organization_id = ?",
                (organization_id,),
            )
            .fetchone()
        )
        return None if row is None else _organization_from_row(row)

    def get_organization_by_slug(self, slug: str) -> Organization | None:
        """Look up by the operator-facing name, normalized the same way it was stored.

        Normalizing here rather than trusting the caller is what keeps the
        UNIQUE constraint and this lookup describing the same set of rows: an
        operator typing ``Acme`` must find the organization stored as ``acme``,
        not create a second one.
        """
        row = (
            self._database.connect()
            .execute(
                _select(ORGANIZATIONS_TABLE, _ORGANIZATION_COLUMNS) + " WHERE slug = ?",
                (normalize_organization_slug(slug),),
            )
            .fetchone()
        )
        return None if row is None else _organization_from_row(row)

    def list_organizations(self) -> tuple[Organization, ...]:
        """Every organization, for operator tooling only.

        Not a tenant-scoped read and never reachable from an authenticated
        request path: it answers "what exists in this installation", which is
        an operator's question and nobody else's.
        """
        rows = (
            self._database.connect()
            .execute(_select(ORGANIZATIONS_TABLE, _ORGANIZATION_COLUMNS) + " ORDER BY slug")
            .fetchall()
        )
        return tuple(_organization_from_row(row) for row in rows)

    # -- users --------------------------------------------------------------

    def create_user(self, user: User, *, now_utc: str | None = None) -> User:
        """Store one user. No credential material is written; there is none."""
        stored = (
            user
            if now_utc is None
            else User(
                user_id=user.user_id,
                email=user.email,
                normalized_email=user.normalized_email,
                display_name=user.display_name,
                status=user.status,
                created_at_utc=now_utc,
            )
        )
        with self._database.transaction() as connection:
            try:
                connection.execute(
                    _insert(USERS_TABLE, _USER_COLUMNS),
                    (
                        stored.user_id,
                        stored.email,
                        stored.normalized_email,
                        stored.display_name,
                        stored.status.value,
                        stored.created_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # The address is not echoed. A duplicate-account error that
                # quotes the address is a membership oracle the moment this
                # path is reachable by anyone but an operator.
                raise DuplicateIdentityError(
                    f"A user with id {stored.user_id!r} or that login handle already exists."
                ) from exc
        return stored

    def get_user(self, user_id: str) -> User | None:
        row = (
            self._database.connect()
            .execute(
                _select(USERS_TABLE, _USER_COLUMNS) + " WHERE user_id = ?",
                (user_id,),
            )
            .fetchone()
        )
        return None if row is None else _user_from_row(row)

    def get_user_by_email(self, email: str) -> User | None:
        """Look up by login handle, normalized by the one rule that stored it.

        The future login path depends on this function and the UNIQUE
        constraint agreeing on what counts as the same address; both go through
        ``normalize_login_email``, so they cannot diverge.
        """
        row = (
            self._database.connect()
            .execute(
                _select(USERS_TABLE, _USER_COLUMNS) + " WHERE normalized_email = ?",
                (normalize_login_email(email),),
            )
            .fetchone()
        )
        return None if row is None else _user_from_row(row)

    # -- memberships --------------------------------------------------------

    def create_membership(
        self,
        membership: OrganizationMembership,
        *,
        now_utc: str | None = None,
    ) -> OrganizationMembership:
        """Join one user to one organization in one role.

        Both referenced rows are verified inside the transaction, so a typo in
        an operator command is reported as "no such organization" rather than
        as a foreign-key failure. The constraints still do the enforcing.
        """
        stored = (
            membership
            if now_utc is None
            else OrganizationMembership(
                membership_id=membership.membership_id,
                organization_id=membership.organization_id,
                user_id=membership.user_id,
                role=membership.role,
                created_at_utc=now_utc,
            )
        )
        with self._database.transaction() as connection:
            self._assert_exists(
                connection,
                table=ORGANIZATIONS_TABLE,
                column="organization_id",
                value=stored.organization_id,
                label="Organization",
            )
            self._assert_exists(
                connection,
                table=USERS_TABLE,
                column="user_id",
                value=stored.user_id,
                label="User",
            )
            try:
                connection.execute(
                    _insert(ORGANIZATION_MEMBERSHIPS_TABLE, _MEMBERSHIP_COLUMNS),
                    (
                        stored.membership_id,
                        stored.organization_id,
                        stored.user_id,
                        stored.role.value,
                        stored.created_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateIdentityError(
                    f"User {stored.user_id} already holds a membership in organization "
                    f"{stored.organization_id}. A user has exactly one role per organization."
                ) from exc
        return stored

    def get_membership(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> OrganizationMembership | None:
        """The row a future request turns into a tenant scope, or None.

        None is the answer for a user who is not a member and for an
        organization that does not exist. The caller cannot tell them apart,
        which is what a non-member should be able to learn: nothing.
        """
        row = (
            self._database.connect()
            .execute(
                _select(ORGANIZATION_MEMBERSHIPS_TABLE, _MEMBERSHIP_COLUMNS)
                + " WHERE organization_id = ? AND user_id = ?",
                (organization_id, user_id),
            )
            .fetchone()
        )
        return None if row is None else _membership_from_row(row)

    def list_memberships_for_user(self, user_id: str) -> tuple[OrganizationMembership, ...]:
        """Every organization this user belongs to, for a future session bootstrap."""
        rows = (
            self._database.connect()
            .execute(
                _select(ORGANIZATION_MEMBERSHIPS_TABLE, _MEMBERSHIP_COLUMNS)
                + " WHERE user_id = ? ORDER BY created_at_utc, membership_id",
                (user_id,),
            )
            .fetchall()
        )
        return tuple(_membership_from_row(row) for row in rows)

    # -- review queues ------------------------------------------------------

    def create_review_queue(
        self,
        queue: ReviewQueue,
        *,
        now_utc: str | None = None,
    ) -> ReviewQueue:
        """Create one queue inside an existing organization.

        The organization must already exist; a queue is not a path through
        which a tenant comes into being. That is the rule that keeps a
        registration command from silently manufacturing a tenant because a
        slug was misspelled.
        """
        stored = (
            queue
            if now_utc is None
            else ReviewQueue(
                review_queue_id=queue.review_queue_id,
                organization_id=queue.organization_id,
                name=queue.name,
                created_at_utc=now_utc,
            )
        )
        with self._database.transaction() as connection:
            self._assert_exists(
                connection,
                table=ORGANIZATIONS_TABLE,
                column="organization_id",
                value=stored.organization_id,
                label="Organization",
            )
            try:
                connection.execute(
                    _insert(REVIEW_QUEUES_TABLE, _QUEUE_COLUMNS),
                    (
                        stored.review_queue_id,
                        stored.organization_id,
                        stored.name,
                        stored.created_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateIdentityError(
                    f"A review queue with id {stored.review_queue_id!r} already exists, or "
                    f"organization {stored.organization_id} already owns a queue named "
                    f"{stored.name!r}."
                ) from exc
        return stored

    def get_review_queue(self, review_queue_id: str) -> ReviewQueue | None:
        row = (
            self._database.connect()
            .execute(
                _select(REVIEW_QUEUES_TABLE, _QUEUE_COLUMNS) + " WHERE review_queue_id = ?",
                (review_queue_id,),
            )
            .fetchone()
        )
        return None if row is None else _queue_from_row(row)

    def get_review_queue_by_name(
        self,
        *,
        organization_id: str,
        name: str,
    ) -> ReviewQueue | None:
        """Resolve an operator-named queue within one organization.

        Scoped by organization, because queue names are unique per tenant and
        two organizations naming a queue the same thing is expected.
        """
        row = (
            self._database.connect()
            .execute(
                _select(REVIEW_QUEUES_TABLE, _QUEUE_COLUMNS)
                + " WHERE organization_id = ? AND name = ?",
                (organization_id, name.strip()),
            )
            .fetchone()
        )
        return None if row is None else _queue_from_row(row)

    def list_review_queues(self, organization_id: str) -> tuple[ReviewQueue, ...]:
        """Every queue one organization owns, oldest first."""
        rows = (
            self._database.connect()
            .execute(
                _select(REVIEW_QUEUES_TABLE, _QUEUE_COLUMNS)
                + " WHERE organization_id = ? ORDER BY created_at_utc, review_queue_id",
                (organization_id,),
            )
            .fetchall()
        )
        return tuple(_queue_from_row(row) for row in rows)

    def list_all_review_queues(self) -> tuple[ReviewQueue, ...]:
        """Every queue in the installation, across every organization.

        The one deliberately unscoped read in this module, and it exists for
        exactly two callers: operator tooling, and the transitional binding
        that lets the still-unauthenticated Sprint 11 API find the single queue
        a local installation holds (see ``review_api.dependencies``). It
        answers an installation-wide question, so it must never be reachable
        from a request whose tenant was decided by a caller. The authenticated
        wiring resolves a queue from a verified membership instead, and this
        method plays no part in it.
        """
        rows = (
            self._database.connect()
            .execute(
                _select(REVIEW_QUEUES_TABLE, _QUEUE_COLUMNS)
                + " ORDER BY created_at_utc, review_queue_id"
            )
            .fetchall()
        )
        return tuple(_queue_from_row(row) for row in rows)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _assert_exists(
        connection: sqlite3.Connection,
        *,
        table: str,
        column: str,
        value: str,
        label: str,
    ) -> None:
        row = connection.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?",  # noqa: S608 - table/column are constants
            (value,),
        ).fetchone()
        if row is None:
            raise IdentityNotFoundError(f"{label} {value} is not stored.")

    def timestamp(self) -> str:
        """The repository's clock, so operator tooling stamps one consistent time."""
        return utc_timestamp(self._clock)
