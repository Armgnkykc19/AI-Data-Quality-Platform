"""SQLite storage for password verifiers.

Four statements and no more: read one, insert one, replace one, and check that
the user exists. There is no list, no search, no delete and no history — a
credential store whose contents can be enumerated is a credential store that
will be enumerated, and nothing in this product needs to ask "who has a
password" as a query.

The stored value is argon2-cffi's encoded string, written and read verbatim.
Nothing here parses it, splits it, or knows what an Argon2 parameter is.
"""

from __future__ import annotations

from identity.credentials import PasswordCredential
from identity.errors import IdentityNotFoundError
from review_persistence.identity_schema import PASSWORD_CREDENTIALS_TABLE, USERS_TABLE
from review_persistence.sqlite.database import Clock, ReviewDatabase, _now

__all__ = ["SqliteCredentialRepository"]

_COLUMNS = ("user_id", "password_hash", "created_at_utc", "updated_at_utc")

_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM {PASSWORD_CREDENTIALS_TABLE} WHERE user_id = ?"

# One statement for both "create" and "replace", because the two writers that
# exist -- operator provisioning and the rehash a successful login performs --
# both mean "this is now the verifier". ON CONFLICT names the columns that may
# move and deliberately omits created_at_utc, so the record of when this user
# first gained a way to log in survives every replacement.
_UPSERT = (
    f"INSERT INTO {PASSWORD_CREDENTIALS_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?) "
    "ON CONFLICT (user_id) DO UPDATE SET "
    "password_hash = excluded.password_hash, updated_at_utc = excluded.updated_at_utc"
)

_SELECT_USER = f"SELECT 1 FROM {USERS_TABLE} WHERE user_id = ?"


class SqliteCredentialRepository:
    """Reads and writes ``password_credentials``.

    Satisfies ``identity.repository.CredentialRepository`` structurally without
    inheriting it, matching ``SqliteReviewCaseRepository`` and the Protocol
    style the rest of this project uses.
    """

    def __init__(self, database: ReviewDatabase, *, clock: Clock = _now) -> None:
        self._database = database
        self._clock = clock

    def get_credential(self, user_id: str) -> PasswordCredential | None:
        row = self._database.connect().execute(_SELECT, (user_id,)).fetchone()
        if row is None:
            return None
        return PasswordCredential(
            user_id=str(row["user_id"]),
            password_hash=str(row["password_hash"]),
            created_at_utc=str(row["created_at_utc"]),
            updated_at_utc=str(row["updated_at_utc"]),
        )

    def set_credential(self, credential: PasswordCredential) -> PasswordCredential:
        """Store or replace this user's verifier, inside one transaction.

        The user's existence is checked explicitly rather than left to the
        foreign key, so a mistyped id is reported as a missing user instead of
        as a constraint name. The foreign key remains the actual enforcement.

        ``created_at_utc`` is preserved on replacement by the ON CONFLICT
        clause, so the value returned is re-read rather than echoed: a caller
        that passed a fresh timestamp for an existing credential gets back what
        was actually stored.
        """
        with self._database.transaction() as connection:
            if connection.execute(_SELECT_USER, (credential.user_id,)).fetchone() is None:
                raise IdentityNotFoundError(
                    f"User {credential.user_id} is not stored; a password credential "
                    "cannot belong to nobody."
                )
            connection.execute(
                _UPSERT,
                (
                    credential.user_id,
                    credential.password_hash,
                    credential.created_at_utc,
                    credential.updated_at_utc,
                ),
            )
            row = connection.execute(_SELECT, (credential.user_id,)).fetchone()

        return PasswordCredential(
            user_id=str(row["user_id"]),
            password_hash=str(row["password_hash"]),
            created_at_utc=str(row["created_at_utc"]),
            updated_at_utc=str(row["updated_at_utc"]),
        )
