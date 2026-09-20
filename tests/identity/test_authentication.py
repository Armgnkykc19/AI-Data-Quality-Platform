"""Login: one generic failure, and real Argon2 work behind every one of them.

Four different things can go wrong -- unknown handle, wrong password, disabled
account, missing credential -- and a caller must not be able to tell them
apart. Two halves make that true, and both are tested here:

* the **answer** is identical, which is easy; and
* the **cost** is comparable, which is not, and is the half that gets skipped.

The second half is verified by spying on the password verifier rather than by
measuring elapsed time. A wall-clock assertion would be flaky on a loaded CI
machine and would prove nothing about *why* the timings matched; asserting that
a verification actually happened proves the defence is present.
"""

from __future__ import annotations

import pytest

from identity.authentication import AuthenticationService
from identity.credentials import PasswordCredential
from identity.errors import AuthenticationFailedError, IdentityValidationError
from identity.models import User, UserStatus
from identity.passwords import Argon2idPasswordHasher
from identity.provisioning import PasswordProvisioningService
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.identity.conftest import (
    PASSWORD,
    USER_EMAIL,
    WRONG_PASSWORD,
    cheap_hasher,
    make_user,
)
from tests.review_persistence.conftest import FrozenClock


class VerifierSpy:
    """A hasher that records what it was asked to verify.

    Wraps a real Argon2id hasher rather than replacing it, so the code under
    test still performs genuine work and the recording is the only addition.
    """

    def __init__(self) -> None:
        self._inner = cheap_hasher()
        self.verified_hashes: list[str] = []
        self.hashed_count = 0

    def hash(self, password: str) -> str:
        self.hashed_count += 1
        return self._inner.hash(password)

    def verify(self, encoded_hash: str, password: str) -> bool:
        self.verified_hashes.append(encoded_hash)
        return self._inner.verify(encoded_hash, password)

    def needs_rehash(self, encoded_hash: str) -> bool:
        return self._inner.needs_rehash(encoded_hash)


class BrokenCredentialRepository:
    """Storage that fails. Not a rejection -- a malfunction."""

    def get_credential(self, user_id: str) -> PasswordCredential | None:
        raise OSError("disk I/O error")

    def set_credential(self, credential: PasswordCredential) -> PasswordCredential:
        raise OSError("disk I/O error")  # pragma: no cover


def service_with(
    spy: VerifierSpy,
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
) -> AuthenticationService:
    return AuthenticationService(users=tenants, credentials=credentials, hasher=spy, clock=clock)


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_an_active_user_with_the_right_password_authenticates(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    authenticated = authentication.authenticate(USER_EMAIL, PASSWORD)

    assert authenticated.user_id == credentialed_user.user_id
    assert authenticated.display_name == credentialed_user.display_name


def test_the_authenticated_identity_carries_no_email_and_no_secret(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    """Minimisation: nothing downstream needs the address, so it is not carried.

    An identity object holding an email is an identity object that will
    eventually be logged or serialised with it.
    """
    authenticated = authentication.authenticate(USER_EMAIL, PASSWORD)
    rendered = repr(authenticated)

    assert USER_EMAIL not in rendered
    assert PASSWORD not in rendered
    assert not hasattr(authenticated, "email")
    assert not hasattr(authenticated, "password_hash")


def test_the_login_handle_is_normalized_the_phase_b_way(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    """The lookup must find the row the UNIQUE constraint would have refused.

    Case and surrounding whitespace fold, because ``normalize_login_email`` is
    what wrote ``normalized_email`` in the first place.
    """
    for spelling in ("ADA@EXAMPLE.COM", "  Ada@Example.com  ", "ada@example.com"):
        assert authentication.authenticate(spelling, PASSWORD).user_id == credentialed_user.user_id


def test_the_password_itself_is_never_normalized(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    authentication: AuthenticationService,
    provisioning: PasswordProvisioningService,
    user: User,
) -> None:
    """The handle folds; the secret does not.

    Folding a password would change what is stored, and the transformation
    would have to be reproduced identically forever.
    """
    provisioning.set_password(user.user_id, "  Mixed Case Password  ")

    assert authentication.authenticate(USER_EMAIL, "  Mixed Case Password  ").user_id
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate(USER_EMAIL, "Mixed Case Password")
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate(USER_EMAIL, "  mixed case password  ")


def test_a_user_with_no_memberships_authenticates(
    authentication: AuthenticationService,
    credentialed_user: User,
    tenants: SqliteTenantRepository,
) -> None:
    """Authentication answers *who*, never *what may they do*.

    A user with no organization logs in successfully and simply has nothing to
    read. That is an authorization outcome, decided later, by code that can see
    the membership table.
    """
    assert tenants.list_memberships_for_user(credentialed_user.user_id) == ()

    assert authentication.authenticate(USER_EMAIL, PASSWORD).user_id == credentialed_user.user_id


# --------------------------------------------------------------------------
# One failure, four causes
# --------------------------------------------------------------------------


def test_a_wrong_password_fails_generically(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate(USER_EMAIL, WRONG_PASSWORD)


def test_an_unknown_handle_fails_generically(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate("nobody@example.com", PASSWORD)


def test_a_disabled_user_cannot_authenticate_even_with_the_right_password(
    tenants: SqliteTenantRepository,
    authentication: AuthenticationService,
    provisioning: PasswordProvisioningService,
) -> None:
    disabled = make_user(
        tenants,
        user_id="USR-test-disabled",
        email="disabled@example.com",
        status=UserStatus.DISABLED,
    )
    provisioning.set_password(disabled.user_id, PASSWORD)

    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate("disabled@example.com", PASSWORD)


def test_a_user_without_a_credential_fails_generically(
    authentication: AuthenticationService,
    user: User,
) -> None:
    """A provisioned user with no password yet. The correct starting state."""
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate(USER_EMAIL, PASSWORD)


def test_all_four_causes_produce_the_same_type_and_message(
    tenants: SqliteTenantRepository,
    authentication: AuthenticationService,
    provisioning: PasswordProvisioningService,
    user: User,
) -> None:
    """The oracle this closes: which of these an address is tells an attacker
    whether a real person is behind it and whether pursuing them is worthwhile.
    """
    provisioning.set_password(user.user_id, PASSWORD)
    disabled = make_user(
        tenants,
        user_id="USR-test-off",
        email="off@example.com",
        status=UserStatus.DISABLED,
    )
    provisioning.set_password(disabled.user_id, PASSWORD)
    no_credential = make_user(tenants, user_id="USR-test-bare", email="bare@example.com")
    assert no_credential.user_id

    attempts = [
        ("nobody@example.com", PASSWORD),  # unknown handle
        (USER_EMAIL, WRONG_PASSWORD),  # wrong password
        ("off@example.com", PASSWORD),  # disabled account
        ("bare@example.com", PASSWORD),  # no credential
    ]

    messages = set()
    for handle, password in attempts:
        with pytest.raises(AuthenticationFailedError) as failure:
            authentication.authenticate(handle, password)
        assert type(failure.value) is AuthenticationFailedError
        messages.add(str(failure.value))

    assert len(messages) == 1, messages


def test_a_malformed_login_handle_fails_the_same_way(
    authentication: AuthenticationService,
    credentialed_user: User,
) -> None:
    """Not an ``IdentityValidationError``. That would separate "you typed
    nonsense" from "that account does not exist", which is the same oracle in
    a smaller form."""
    for handle in ("", "not-an-address", "two@@example.com"):
        with pytest.raises(AuthenticationFailedError):
            authentication.authenticate(handle, PASSWORD)


# --------------------------------------------------------------------------
# The dummy verification path
# --------------------------------------------------------------------------


def test_an_unknown_handle_still_performs_a_real_verification(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
) -> None:
    """Without this, an unknown address is refused in microseconds while a
    known one costs tens of milliseconds -- a difference measurable over a few
    requests, which turns login into a "does this person have an account?" API.
    """
    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)

    with pytest.raises(AuthenticationFailedError):
        service.authenticate("nobody@example.com", PASSWORD)

    assert len(spy.verified_hashes) == 1
    assert spy.verified_hashes[0].startswith("$argon2id$")


def test_a_malformed_handle_also_performs_a_verification(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
) -> None:
    """Returning early on a syntactically bad address would make it the one
    input that is refused instantly."""
    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)

    with pytest.raises(AuthenticationFailedError):
        service.authenticate("not-an-address", PASSWORD)

    assert len(spy.verified_hashes) == 1


def test_a_user_without_a_credential_still_performs_a_verification(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
    user: User,
) -> None:
    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)

    with pytest.raises(AuthenticationFailedError):
        service.authenticate(USER_EMAIL, PASSWORD)

    assert len(spy.verified_hashes) == 1


def test_a_disabled_user_is_checked_against_its_real_credential_first(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
    provisioning: PasswordProvisioningService,
) -> None:
    """Status is checked *after* verification, not before.

    Checking first would refuse a disabled account without paying the Argon2
    cost, making "this address exists but is switched off" measurable.
    """
    disabled = make_user(
        tenants,
        user_id="USR-test-disabled",
        email="disabled@example.com",
        status=UserStatus.DISABLED,
    )
    provisioning.set_password(disabled.user_id, PASSWORD)
    stored = credentials.get_credential(disabled.user_id)
    assert stored is not None

    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)
    with pytest.raises(AuthenticationFailedError):
        service.authenticate("disabled@example.com", PASSWORD)

    # The real stored verifier, not the dummy one.
    assert spy.verified_hashes == [stored.password_hash]


def test_the_dummy_hash_is_computed_once_and_reused(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
) -> None:
    """Hashing per failed login would make failure *more* expensive than
    success -- the same signal inverted -- and would hand an attacker a cheap
    way to burn the server's memory-hard budget.
    """
    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)

    for _ in range(4):
        with pytest.raises(AuthenticationFailedError):
            service.authenticate("nobody@example.com", PASSWORD)

    assert spy.hashed_count == 1
    assert len(set(spy.verified_hashes)) == 1


def test_the_dummy_hash_is_not_a_real_users_credential(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
    credentialed_user: User,
) -> None:
    """It is a freshly random secret, never stored, and never anyone's password."""
    spy = VerifierSpy()
    service = service_with(spy, tenants, credentials, clock)
    stored = credentials.get_credential(credentialed_user.user_id)
    assert stored is not None

    with pytest.raises(AuthenticationFailedError):
        service.authenticate("nobody@example.com", PASSWORD)

    assert spy.verified_hashes[0] != stored.password_hash


# --------------------------------------------------------------------------
# Storage failure is not a rejected credential
# --------------------------------------------------------------------------


def test_a_storage_failure_is_not_reported_as_a_bad_password(
    tenants: SqliteTenantRepository,
    clock: FrozenClock,
    user: User,
) -> None:
    """A database that cannot be read has not rejected anyone's credentials.

    Reporting it as a wrong password would send an operator to investigate the
    user instead of the disk, and would hide an outage behind a login form.
    """
    service = AuthenticationService(
        users=tenants,
        credentials=BrokenCredentialRepository(),
        hasher=cheap_hasher(),
        clock=clock,
    )

    with pytest.raises(OSError):
        service.authenticate(USER_EMAIL, PASSWORD)


# --------------------------------------------------------------------------
# Rehash on successful login
# --------------------------------------------------------------------------


def test_a_successful_login_upgrades_a_weakly_hashed_credential(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
    user: User,
) -> None:
    """The one moment the plaintext is available to produce a stronger hash."""
    weak = Argon2idPasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    strong = Argon2idPasswordHasher(time_cost=3, memory_cost=16, parallelism=1)
    PasswordProvisioningService(credentials=credentials, hasher=weak, clock=clock).set_password(
        user.user_id, PASSWORD
    )
    before = credentials.get_credential(user.user_id)
    assert before is not None

    service = AuthenticationService(
        users=tenants, credentials=credentials, hasher=strong, clock=clock
    )
    assert service.authenticate(USER_EMAIL, PASSWORD).user_id == user.user_id

    after = credentials.get_credential(user.user_id)
    assert after is not None
    assert after.password_hash != before.password_hash
    assert strong.needs_rehash(after.password_hash) is False
    # The upgraded credential still verifies the same password.
    assert strong.verify(after.password_hash, PASSWORD) is True
    # First-provisioned time is preserved; only the update stamp moves.
    assert after.created_at_utc == before.created_at_utc


def test_a_rehash_keeps_working_for_a_password_that_predates_a_tighter_policy(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    clock: FrozenClock,
    user: User,
) -> None:
    """The policy governs what may be *set*, not what may be re-encoded.

    Re-applying it during a rehash would lock out a user whose password became
    too short only because the minimum was raised afterwards -- punishing them
    for a change they had no part in.
    """
    short = "shortpass"
    assert len(short) < 12
    weak = Argon2idPasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    strong = Argon2idPasswordHasher(time_cost=3, memory_cost=16, parallelism=1)
    # Written directly, as a credential predating the current policy would be.
    credentials.set_credential(
        PasswordCredential(
            user_id=user.user_id,
            password_hash=weak.hash(short),
            created_at_utc="2026-01-01T00:00:00Z",
            updated_at_utc="2026-01-01T00:00:00Z",
        )
    )

    service = AuthenticationService(
        users=tenants, credentials=credentials, hasher=strong, clock=clock
    )

    assert service.authenticate(USER_EMAIL, short).user_id == user.user_id
    # And setting a new one still goes through the policy.
    with pytest.raises(IdentityValidationError):
        PasswordProvisioningService(
            credentials=credentials, hasher=strong, clock=clock
        ).set_password(user.user_id, short)


def test_a_failed_login_never_touches_the_credential(
    authentication: AuthenticationService,
    credentials: SqliteCredentialRepository,
    credentialed_user: User,
) -> None:
    before = credentials.get_credential(credentialed_user.user_id)

    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate(USER_EMAIL, WRONG_PASSWORD)
    with pytest.raises(AuthenticationFailedError):
        authentication.authenticate("nobody@example.com", PASSWORD)

    assert credentials.get_credential(credentialed_user.user_id) == before


def test_a_successful_login_leaves_a_current_hash_alone(
    authentication: AuthenticationService,
    credentials: SqliteCredentialRepository,
    credentialed_user: User,
) -> None:
    """No rehash is needed, so nothing is written."""
    before = credentials.get_credential(credentialed_user.user_id)

    authentication.authenticate(USER_EMAIL, PASSWORD)

    assert credentials.get_credential(credentialed_user.user_id) == before
