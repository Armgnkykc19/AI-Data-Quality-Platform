"""Login end to end, and the secrets that must never surface.

Two concerns in one file because they are the same concern from two angles:
what the login path produces, and what it must never let escape. The second
half is the one that rots quietly -- a dataclass gains a field, a ``repr`` is
regenerated, and a bearer token starts appearing in log lines with nothing
failing.
"""

from __future__ import annotations

import pytest

from identity.errors import AuthenticationFailedError
from identity.login import LoginService
from identity.models import User, UserStatus
from identity.provisioning import PasswordProvisioningService
from identity.session_service import SessionService
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase
from review_persistence.sqlite.session_repository import SqliteSessionRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.identity.conftest import PASSWORD, USER_EMAIL, WRONG_PASSWORD, make_user

# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


def test_login_authenticates_and_starts_a_session(
    login: LoginService,
    sessions: SessionService,
    credentialed_user: User,
) -> None:
    result = login.login(USER_EMAIL, PASSWORD)

    assert result.user.user_id == credentialed_user.user_id
    assert result.created.session.user_id == credentialed_user.user_id
    # The token it returned is immediately usable.
    resolved = sessions.resolve_session(result.created.raw_token)
    assert resolved.session.session_id == result.created.session.session_id


def test_a_failed_login_writes_no_session(
    login: LoginService,
    database: ReviewDatabase,
    credentialed_user: User,
) -> None:
    """Nothing is written until the password has been verified.

    A failed attempt must leave no session row and no trace that the handle was
    tried against a real account -- otherwise the session table becomes a
    record of who someone tried to impersonate.
    """
    for handle, password in (
        (USER_EMAIL, WRONG_PASSWORD),
        ("nobody@example.com", PASSWORD),
    ):
        with pytest.raises(AuthenticationFailedError):
            login.login(handle, password)

    assert database.connect().execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0


def test_a_disabled_user_gets_no_session_from_login(
    login: LoginService,
    tenants: SqliteTenantRepository,
    provisioning: PasswordProvisioningService,
    database: ReviewDatabase,
) -> None:
    disabled = make_user(
        tenants, user_id="USR-test-off", email="off@example.com", status=UserStatus.DISABLED
    )
    provisioning.set_password(disabled.user_id, PASSWORD)

    with pytest.raises(AuthenticationFailedError):
        login.login("off@example.com", PASSWORD)

    assert database.connect().execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0


def test_a_user_with_no_membership_logs_in(
    login: LoginService,
    tenants: SqliteTenantRepository,
    credentialed_user: User,
) -> None:
    """Login answers *who*; having nothing to read is an authorization outcome."""
    assert tenants.list_memberships_for_user(credentialed_user.user_id) == ()

    assert login.login(USER_EMAIL, PASSWORD).created.raw_token


def test_two_logins_produce_two_independent_sessions(
    login: LoginService,
    sessions: SessionService,
    credentialed_user: User,
) -> None:
    """Revoking one must not end the other -- two browsers, two sessions."""
    first = login.login(USER_EMAIL, PASSWORD)
    second = login.login(USER_EMAIL, PASSWORD)

    assert first.created.raw_token != second.created.raw_token
    sessions.revoke_session(first.created.raw_token)

    assert sessions.resolve_session(second.created.raw_token) is not None


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------


def test_provisioning_replaces_an_existing_password(
    provisioning: PasswordProvisioningService,
    login: LoginService,
    credentialed_user: User,
) -> None:
    """The narrow replacement a later operator command will call behind getpass."""
    new_password = "a completely different secret"

    provisioning.set_password(credentialed_user.user_id, new_password)

    assert login.login(USER_EMAIL, new_password).user.user_id == credentialed_user.user_id
    with pytest.raises(AuthenticationFailedError):
        login.login(USER_EMAIL, PASSWORD)


def test_provisioning_rejects_a_password_before_hashing_it(
    provisioning: PasswordProvisioningService,
    credentials: SqliteCredentialRepository,
    user: User,
) -> None:
    """A rejected password never reaches the hasher and never reaches storage.

    Unlike the rehash path, setting a password *is* what the policy governs.
    """
    from identity.errors import IdentityValidationError

    with pytest.raises(IdentityValidationError):
        provisioning.set_password(user.user_id, "short")

    assert credentials.get_credential(user.user_id) is None


def test_there_is_no_default_credential(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    user: User,
) -> None:
    """A freshly created user cannot log in until an operator gives them a way.

    That is the correct starting state for an internal tool: no seeded account,
    no default password, nothing to forget to change.
    """
    assert credentials.get_credential(user.user_id) is None


# --------------------------------------------------------------------------
# Secrets must not surface
# --------------------------------------------------------------------------


def test_no_secret_appears_in_the_login_results_representation(
    login: LoginService,
    credentialed_user: User,
) -> None:
    result = login.login(USER_EMAIL, PASSWORD)

    for rendered in (repr(result), str(result), repr(result.created), repr(result.user)):
        assert result.created.raw_token not in rendered
        assert result.created.session.token_hash not in rendered
        assert PASSWORD not in rendered


def test_no_secret_appears_in_a_resolved_sessions_representation(
    login: LoginService,
    sessions: SessionService,
    credentialed_user: User,
) -> None:
    result = login.login(USER_EMAIL, PASSWORD)

    rendered = repr(sessions.resolve_session(result.created.raw_token))

    assert result.created.raw_token not in rendered
    assert result.created.session.token_hash not in rendered


def test_no_secret_appears_in_an_authentication_failure(
    login: LoginService,
    credentialed_user: User,
) -> None:
    """The message a future HTTP layer might render must carry nothing."""
    with pytest.raises(AuthenticationFailedError) as failure:
        login.login(USER_EMAIL, WRONG_PASSWORD)

    for rendered in (str(failure.value), repr(failure.value)):
        assert WRONG_PASSWORD not in rendered
        assert USER_EMAIL not in rendered


def test_no_secret_appears_in_a_session_failure(
    login: LoginService,
    sessions: SessionService,
    credentialed_user: User,
) -> None:
    from identity.errors import SessionRevokedError

    result = login.login(USER_EMAIL, PASSWORD)
    sessions.revoke_session(result.created.raw_token)

    with pytest.raises(SessionRevokedError) as failure:
        sessions.resolve_session(result.created.raw_token)

    assert result.created.raw_token not in str(failure.value)
    assert result.created.session.token_hash not in str(failure.value)


def test_the_user_model_still_carries_no_credential_field() -> None:
    """Phase B's promise, re-checked now that credentials actually exist.

    The temptation at this point is to add ``password_hash`` to ``User``
    because it is convenient. It would mean every read of a display name
    carried a verifier.
    """
    fields = set(User.__dataclass_fields__)

    assert not {name for name in fields if "password" in name or "secret" in name}


def test_the_raw_token_is_nowhere_in_the_database(
    login: LoginService,
    database: ReviewDatabase,
    credentialed_user: User,
) -> None:
    """Swept across every table, not only the one that should hold it.

    A stolen copy of this file must yield no usable credential.
    """
    result = login.login(USER_EMAIL, PASSWORD)
    connection = database.connect()

    tables = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 - names from schema
            for value in tuple(row):
                assert value != result.created.raw_token
                assert not (isinstance(value, str) and PASSWORD in value)


def test_the_stored_verifier_is_not_the_password(
    credentials: SqliteCredentialRepository,
    credentialed_user: User,
) -> None:
    stored = credentials.get_credential(credentialed_user.user_id)

    assert stored is not None
    assert stored.password_hash != PASSWORD
    assert PASSWORD not in stored.password_hash
    assert stored.password_hash.startswith("$argon2id$")


def test_a_session_resolves_only_through_its_token_not_its_id(
    login: LoginService,
    sessions: SessionService,
    session_store: SqliteSessionRepository,
    credentialed_user: User,
) -> None:
    """The session id is safe to log precisely because it is not an access path.

    Presenting it where a token belongs must fail; otherwise every audit record
    naming a session would be a credential.
    """
    from identity.errors import SessionNotFoundError

    result = login.login(USER_EMAIL, PASSWORD)

    assert session_store.get_session(result.created.session.session_id) is not None
    with pytest.raises(SessionNotFoundError):
        sessions.resolve_session(result.created.session.session_id)
