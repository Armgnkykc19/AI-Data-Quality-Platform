"""A real authenticated API, wired the way production wires one.

Every fixture here builds the genuine services over a real SQLite database in
``tmp_path``: the real Argon2id hasher, the real session service, the real
login orchestration, the real SQLite repositories. Nothing about authentication
is faked, because the things worth testing -- that a wrong password and an
unknown address are indistinguishable, that a cookie is ``HttpOnly``, that
renewal never moves the absolute bound -- are all properties of the real
composition.

Two substitutions, both by injection and neither touching production defaults:

* **The clock.** Session boundaries are 60 minutes and 8 hours; a test that
  waited for them would take eight hours. ``FrozenClock`` is the project's
  existing abstraction.
* **The Argon2 cost.** Production hashing is memory-hard by design and costs
  tens of milliseconds; a suite paying that on every login would be unusable.
  The parameters are lowered through the hasher's constructor, so
  ``Argon2idPasswordHasher()`` with no arguments -- what the lifespan builds --
  is untouched.

**Two database connections, deliberately.** ``TestClient`` runs the
application on its own thread, and a ``sqlite3`` connection is legal only on
the thread that created it -- the same constraint
``test_sqlite_read_integration`` already documents. So the server opens its own
connection inside a lifespan, on the serving thread, exactly as uvicorn does;
and the test keeps a separate *control* connection for setting up users,
advancing state and reading rows back. WAL means each sees the other's commits.

Both connections share one ``FrozenClock``, so advancing time in a test
advances it for the server too.

Every path is under ``tmp_path``, so no test here can open
``storage/review_queue.db``.

The ``pytest`` fixtures that wrap ``build_auth_fixture`` live in this package's
``conftest.py`` rather than here: pytest discovers fixtures in conftest files,
and registering a module as a plugin is only permitted from the top-level one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.authentication import AuthenticationService
from identity.login import LoginService
from identity.models import User, UserStatus
from identity.provisioning import UserProvisioningService
from identity.session_service import SessionService
from review_api import create_app
from review_api.auth_config import AuthHttpConfig
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.provisioning_repository import SqliteUserProvisioningRepository
from review_persistence.sqlite.session_repository import SqliteSessionRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.identity.conftest import cheap_hasher
from tests.review_persistence.conftest import FrozenClock

# The origin the configured application trusts. Everything else must be refused.
ALLOWED_ORIGIN = "http://127.0.0.1:5173"
FOREIGN_ORIGIN = "http://evil.test"

COOKIE_NAME = "dq_session"

EMAIL = "reviewer@example.test"
DISPLAY_NAME = "Reviewer One"
PASSWORD = "correct horse battery staple"
WRONG_PASSWORD = "incorrect horse battery staple"

MINUTE = 60
HOUR = 60 * MINUTE


@dataclass
class AuthFixture:
    """Everything a test needs to drive and inspect the authenticated API.

    ``database``, ``sessions``, ``tenants`` and ``provisioning`` all speak
    through the test's own control connection, never the server's. That is what
    lets a test provision a user, revoke a session or read a row back while the
    application is running on another thread.
    """

    client: TestClient
    database: ReviewDatabase
    clock: FrozenClock
    sessions: SessionService
    tenants: SqliteTenantRepository
    provisioning: UserProvisioningService
    config: AuthHttpConfig

    # -- helpers ------------------------------------------------------------

    def create_user(
        self,
        *,
        email: str = EMAIL,
        display_name: str = DISPLAY_NAME,
        password: str = PASSWORD,
    ) -> User:
        """Provision a login the way the operator command does."""
        return self.provisioning.provision_user(
            email=email, display_name=display_name, password=password
        )

    def login(
        self,
        *,
        email: str = EMAIL,
        password: str = PASSWORD,
        origin: str | None = ALLOWED_ORIGIN,
    ):
        headers = {} if origin is None else {"Origin": origin}
        return self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=headers,
        )

    def login_as(self, **kwargs) -> str:
        """Log in successfully and return the raw cookie value."""
        response = self.login(**kwargs)
        assert response.status_code == 200, response.text
        token = response.cookies.get(COOKIE_NAME)
        assert token is not None
        return token

    def set_cookie(self, token: str | None) -> None:
        """Put a token in the client's jar, or empty it."""
        self.client.cookies.clear()
        if token is not None:
            self.client.cookies.set(COOKIE_NAME, token)

    def get_session(self):
        return self.client.get("/api/v1/auth/session")

    def continue_session(self, *, origin: str | None = ALLOWED_ORIGIN):
        headers = {} if origin is None else {"Origin": origin}
        return self.client.post("/api/v1/auth/session/continue", headers=headers)

    def logout(self, *, origin: str | None = ALLOWED_ORIGIN):
        headers = {} if origin is None else {"Origin": origin}
        return self.client.post("/api/v1/auth/logout", headers=headers)

    def stored_session(self, token: str):
        """Read the session row straight from storage, bypassing the service."""
        from identity.sessions import hash_session_token

        return SqliteSessionRepository(self.database).get_session_by_token_hash(
            hash_session_token(token)
        )

    def session_count(self) -> int:
        return self.database.connect().execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]

    def tolerant_client(self) -> TestClient:
        """A client that returns a 500 instead of re-raising the exception.

        Starlette's server-error middleware builds the response and then
        re-raises so the failure reaches the server log; ``TestClient``
        re-raises it into the test by default. This is the project's existing
        pattern for asserting on a 500 body -- see ``probe_client``.

        Built over the same application without entering its context manager,
        so the lifespan does not run a second time and the services the first
        client wired are still the ones serving.
        """
        return TestClient(self.client.app, raise_server_exceptions=False)

    def break_credential_storage(self) -> None:
        """Make the server's next credential read fail as a storage fault.

        Dropping the table is the cross-connection way to simulate a database
        that cannot be read: closing the test's own connection would not touch
        the server's. What matters is that the failure is a malfunction rather
        than a rejected credential, and that the API says so.
        """
        self.database.connect().execute("DROP TABLE password_credentials")


def build_auth_fixture(
    tmp_path: Path,
    *,
    cookie_secure: bool = False,
    allowed_origins: tuple[str, ...] = (ALLOWED_ORIGIN,),
) -> Iterator[AuthFixture]:
    """Build the whole authenticated application over one temporary database.

    ``cookie_secure`` defaults to False here only because ``TestClient`` speaks
    ``http``; a Secure cookie would be set but never returned, so every
    authenticated test would fail for a reason unrelated to what it checks. One
    test builds the fixture with ``cookie_secure=True`` and asserts the
    attribute appears, which is what keeps this default from hiding a bug.
    """
    clock = FrozenClock()
    hasher = cheap_hasher()
    persistence = ReviewPersistenceConfig(
        database_path=tmp_path / "auth.db", busy_timeout_ms=2000, journal_mode="WAL"
    )
    config = AuthHttpConfig(
        session_cookie_name=COOKIE_NAME,
        session_cookie_secure=cookie_secure,
        allowed_origins=allowed_origins,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the server's own connection on the serving thread.

        Mirrors ``production_lifespan`` against a temporary database. Opening
        here rather than handing in a connection built by the test is what
        keeps every server-side query on the thread that created its
        connection.
        """
        server_database = open_review_database(persistence, clock=clock)
        try:
            server_users = SqliteTenantRepository(server_database)
            server_sessions = SessionService(
                sessions=SqliteSessionRepository(server_database),
                users=server_users,
                clock=clock,
            )
            app.state.session_service = server_sessions
            app.state.login_service = LoginService(
                authentication=AuthenticationService(
                    users=server_users,
                    credentials=SqliteCredentialRepository(server_database),
                    hasher=hasher,
                    clock=clock,
                ),
                sessions=server_sessions,
            )
            yield
        finally:
            app.state.session_service = None
            app.state.login_service = None
            server_database.close()

    # The control connection. Created first so the schema exists before the
    # server opens its own, and used by the test for everything the server is
    # not doing.
    control = open_review_database(persistence, clock=clock)
    control_users = SqliteTenantRepository(control)
    app = create_app(auth_config=config, lifespan=lifespan)
    try:
        with TestClient(app) as client:
            yield AuthFixture(
                client=client,
                database=control,
                clock=clock,
                sessions=SessionService(
                    sessions=SqliteSessionRepository(control),
                    users=control_users,
                    clock=clock,
                ),
                tenants=control_users,
                provisioning=UserProvisioningService(
                    provisioning=SqliteUserProvisioningRepository(control),
                    hasher=hasher,
                    clock=clock,
                ),
                config=config,
            )
    finally:
        control.close()


def disable(auth: AuthFixture, user: User) -> None:
    """Switch an account off the way an operator eventually will."""
    auth.database.connect().execute(
        "UPDATE users SET status = ? WHERE user_id = ?",
        (UserStatus.DISABLED.value, user.user_id),
    )
