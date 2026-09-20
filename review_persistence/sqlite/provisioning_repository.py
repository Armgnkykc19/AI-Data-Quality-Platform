"""Creating a user and their password in one transaction.

This exists for a single guarantee: an operator provisioning a login either
produces a user who can sign in, or produces nothing at all. The state in
between -- a user row with no credential -- is what we refuse to be able to
create, because it is silent. Nothing errors, nothing looks wrong, and the
person simply cannot log in for a reason no one can see in the database.

It is a composition rather than a third copy of two INSERTs. ``BEGIN
IMMEDIATE`` cannot nest, so the two repositories cannot each open their own
transaction inside a third; instead each exposes a connection-level helper and
this module opens the one transaction that wraps both. The SQL still lives with
the table it writes.

Deliberately narrow: one method. There is no update, no delete, and no
"provision or replace" -- replacing a password is
``PasswordProvisioningService``, and it is a different decision with different
rules.
"""

from __future__ import annotations

from identity.credentials import PasswordCredential
from identity.models import User
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository

__all__ = ["SqliteUserProvisioningRepository"]


class SqliteUserProvisioningRepository:
    """Writes a user and their credential together, or neither."""

    def __init__(self, database: ReviewDatabase) -> None:
        self._database = database

    def create_user_with_credential(
        self,
        user: User,
        credential: PasswordCredential,
    ) -> User:
        """Insert both rows in one transaction.

        A duplicate login handle raises from the user insert before the
        credential is written, and a failure in either statement rolls the
        whole transaction back -- so a refused provisioning leaves the database
        exactly as it was.

        The foreign key from the credential to the user is satisfied by
        ordering: the user is inserted first, inside the same transaction, so
        the reference exists by the time it is checked.
        """
        with self._database.transaction() as connection:
            SqliteTenantRepository.insert_user_in(connection, user)
            SqliteCredentialRepository.upsert_credential_in(connection, credential)
        return user
