"""Fixtures for the authentication and session tests.

Everything here is wired to the **real** SQLite repositories against a
``tmp_path`` database rather than to in-memory fakes. Session expiry,
monotonic activity and revocation are enforced partly by SQL predicates and
partly by CHECK constraints, so a fake repository would let the service's
logic pass tests that the actual storage would refuse -- which is the one
failure mode these tests exist to prevent.

Two things are deliberately substituted:

* **The clock.** Sessions are defined by 60-minute and 8-hour boundaries, and a
  test that waited for them would take eight hours. ``FrozenClock`` is the
  project's existing abstraction, reused rather than reinvented.
* **The Argon2 parameters.** Production hashing is memory-hard on purpose and
  costs tens of milliseconds per verification; a suite that paid that hundreds
  of times would be unusable. The cost is lowered *by injection*, through the
  constructor, so production defaults are never touched -- and one test asserts
  the production hasher still uses the library's own parameters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from identity.authentication import AuthenticationService
from identity.login import LoginService
from identity.models import User, UserStatus
from identity.passwords import Argon2idPasswordHasher
from identity.provisioning import PasswordProvisioningService
from identity.session_service import SessionService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.session_repository import SqliteSessionRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.review_persistence.conftest import FrozenClock

# A password comfortably over the 12-character minimum, so tests that are not
# about the policy never accidentally trip it.
PASSWORD = "correct horse battery staple"
WRONG_PASSWORD = "incorrect horse battery staple"

USER_ID = "USR-test-ada"
USER_EMAIL = "ada@example.com"
PROVISIONED_AT = "2026-09-12T07:00:00Z"


def cheap_hasher() -> Argon2idPasswordHasher:
    """Argon2id at the lowest cost the library accepts.

    Injected rather than configured globally: production constructs
    ``Argon2idPasswordHasher()`` with no arguments and gets argon2-cffi's own
    parameters, which these values never touch.
    """
    return Argon2idPasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def persistence_config(tmp_path: Path) -> ReviewPersistenceConfig:
    """Always tmp_path. No test may touch storage/review_queue.db."""
    return ReviewPersistenceConfig(
        database_path=tmp_path / "identity.db",
        busy_timeout_ms=2000,
        journal_mode="WAL",
    )


@pytest.fixture
def database(
    persistence_config: ReviewPersistenceConfig,
    clock: FrozenClock,
) -> Iterator[ReviewDatabase]:
    db = open_review_database(persistence_config, clock=clock)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def tenants(database: ReviewDatabase) -> SqliteTenantRepository:
    return SqliteTenantRepository(database)


@pytest.fixture
def credentials(database: ReviewDatabase) -> SqliteCredentialRepository:
    return SqliteCredentialRepository(database)


@pytest.fixture
def session_store(database: ReviewDatabase) -> SqliteSessionRepository:
    return SqliteSessionRepository(database)


@pytest.fixture
def hasher() -> Argon2idPasswordHasher:
    return cheap_hasher()


def make_user(
    tenants: SqliteTenantRepository,
    *,
    user_id: str = USER_ID,
    email: str = USER_EMAIL,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    return tenants.create_user(
        User.create(
            email=email,
            display_name="Ada",
            created_at_utc=PROVISIONED_AT,
            user_id=user_id,
            status=status,
        )
    )


@pytest.fixture
def user(tenants: SqliteTenantRepository) -> User:
    """An active user with no memberships, which is enough to authenticate."""
    return make_user(tenants)


@pytest.fixture
def provisioning(
    credentials: SqliteCredentialRepository,
    hasher: Argon2idPasswordHasher,
    clock: FrozenClock,
) -> PasswordProvisioningService:
    return PasswordProvisioningService(credentials=credentials, hasher=hasher, clock=clock)


@pytest.fixture
def credentialed_user(
    user: User,
    provisioning: PasswordProvisioningService,
) -> User:
    """A user who can actually log in. Provisioned the way an operator would."""
    provisioning.set_password(user.user_id, PASSWORD)
    return user


@pytest.fixture
def authentication(
    tenants: SqliteTenantRepository,
    credentials: SqliteCredentialRepository,
    hasher: Argon2idPasswordHasher,
    clock: FrozenClock,
) -> AuthenticationService:
    return AuthenticationService(
        users=tenants,
        credentials=credentials,
        hasher=hasher,
        clock=clock,
    )


@pytest.fixture
def sessions(
    session_store: SqliteSessionRepository,
    tenants: SqliteTenantRepository,
    clock: FrozenClock,
) -> SessionService:
    return SessionService(sessions=session_store, users=tenants, clock=clock)


@pytest.fixture
def login(
    authentication: AuthenticationService,
    sessions: SessionService,
) -> LoginService:
    return LoginService(authentication=authentication, sessions=sessions)
