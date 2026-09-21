"""A real authenticated, tenant-scoped API, wired the way production wires one.

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

The tenant layer is wired here too, and identically to ``production_lifespan``:
a ``SqliteReviewQueueBinder`` that builds a queue-scoped repository per
authorized request, and a ``TenantAuthorizationService`` over the real tenant
tables. No queue is resolved at startup, so a fixture may create as many as it
likes. The helpers on ``AuthFixture`` create organizations, queues, users,
memberships and workflows through the same primitives the operator CLI uses.

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

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.models import ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import ReviewWorkflowState
from human_review.reporting import resolution_snapshot
from identity.authentication import AuthenticationService
from identity.login import LoginService
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
    UserStatus,
)
from identity.provisioning import UserProvisioningService
from identity.session_service import ResolvedSession, SessionService
from identity.sessions import Session, SessionPolicy
from review_api import create_app
from review_api.auth_config import AuthHttpConfig
from review_api.dependencies import SqliteReviewQueueBinder
from review_api.tenancy import TenantAuthorizationService
from review_application.queues import ReviewQueue
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import ReviewDatabase, open_review_database
from review_persistence.sqlite.provisioning_repository import SqliteUserProvisioningRepository
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
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

PROVISIONED_AT = "2026-09-14T07:00:00Z"


class CountingSessionService:
    """A ``SessionService`` that records how often each operation was asked for.

    The instrumentation behind the one-touch guarantee. Timestamps cannot prove
    it on their own: the fixtures run on a frozen clock, so two renewals inside
    one request would write the same value twice and look exactly like one.
    Counting the calls is the only thing that distinguishes them.

    It wraps rather than subclasses, and it is installed only as the
    application's session service. ``LoginService`` keeps the unwrapped
    instance, so the counters describe what the *guard chain* did on a request
    and nothing else.
    """

    def __init__(self, inner: SessionService) -> None:
        self._inner = inner
        self.resolve_calls = 0
        self.touch_calls = 0
        self.revoke_calls = 0

    @property
    def policy(self) -> SessionPolicy:
        return self._inner.policy

    def reset(self) -> None:
        self.resolve_calls = self.touch_calls = self.revoke_calls = 0

    def resolve_session(self, raw_token: str) -> ResolvedSession:
        self.resolve_calls += 1
        return self._inner.resolve_session(raw_token)

    def touch_session(self, raw_token: str) -> Session:
        self.touch_calls += 1
        return self._inner.touch_session(raw_token)

    def revoke_session(self, raw_token: str) -> Session:
        self.revoke_calls += 1
        return self._inner.revoke_session(raw_token)


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
    counters: CountingSessionService

    # -- tenant graph -------------------------------------------------------
    #
    # All of these write through the control connection while the server is
    # running. WAL means the server's next read sees them, which is what lets a
    # test change a membership mid-session and watch authorization change with
    # it -- the thing a role cached at login could never do.

    def create_organization(self, slug: str) -> Organization:
        return self.tenants.create_organization(
            Organization.create(slug=slug, display_name=slug, created_at_utc=PROVISIONED_AT)
        )

    def suspend(self, organization: Organization) -> None:
        """Suspend a tenant by writing the authoritative column.

        ``SqliteTenantRepository`` exposes no status-mutation method and the
        closeout did not add one -- suspension tooling belongs to the phase that
        needs it. Writing the column directly is also the honest setup for an
        authorization test: it produces a row the persistence layer already
        accepts (``create_organization`` takes any ``OrganizationStatus``, and
        ``test_tenant_persistence`` round-trips a SUSPENDED one), and it proves
        the check reads current stored state rather than something handed to it.
        """
        self.database.connect().execute(
            "UPDATE organizations SET status = ? WHERE organization_id = ?",
            (OrganizationStatus.SUSPENDED.value, organization.organization_id),
        )

    def create_queue(self, organization: Organization, name: str = "default") -> ReviewQueue:
        return self.tenants.create_review_queue(
            ReviewQueue.create(
                organization_id=organization.organization_id,
                name=name,
                created_at_utc=PROVISIONED_AT,
            )
        )

    def grant(
        self,
        user: User,
        organization: Organization,
        role: MembershipRole,
    ) -> OrganizationMembership:
        """The operator action, through the production persistence primitive."""
        return self.tenants.create_membership(
            OrganizationMembership.create(
                organization_id=organization.organization_id,
                user_id=user.user_id,
                role=role,
                created_at_utc=PROVISIONED_AT,
            )
        )

    def set_role(self, user: User, organization: Organization, role: MembershipRole) -> None:
        """Change a stored role out of band, the way ``disable`` changes a status.

        There is no production role-mutation primitive, deliberately, and Phase
        E did not add one to make a test convenient. Writing the authoritative
        row directly is also the stronger setup: it proves authorization reads
        current state rather than something a service handed it.
        """
        self.database.connect().execute(
            "UPDATE organization_memberships SET role = ? "
            "WHERE organization_id = ? AND user_id = ?",
            (role.value, organization.organization_id, user.user_id),
        )

    def repository_for(self, queue: ReviewQueue) -> SqliteReviewCaseRepository:
        """A control-side repository bound to one queue, for seeding and verifying."""
        return SqliteReviewCaseRepository(self.database, review_queue_id=queue.review_queue_id)

    def register_workflow(
        self,
        queue: ReviewQueue,
        resolution: ResolutionResult,
    ) -> ReviewWorkflowState:
        """Fill one queue from a real generated workflow.

        Goes through ``register_workflow`` with the real Sprint 08 reduction, so
        the queue holds the authorization context a resolution will be judged
        against rather than a hand-built row. Returns the generated state, so a
        caller has the real ``ReviewCase`` objects and not only their ids.
        """
        state = generate_review_cases(resolution, config=load_entity_resolution_config())
        assert state.cases, "Fixture resolution must produce at least one REVIEW case."
        self.repository_for(queue).register_workflow(
            state,
            entity_records=resolution.records,
            resolution_snapshot=resolution_snapshot(resolution),
            entity_resolution_config_path="configs/entity_resolution.yaml",
            now_utc=PROVISIONED_AT,
        )
        return state

    @staticmethod
    def cases_url(organization: Organization, queue: ReviewQueue) -> str:
        return (
            f"/api/v1/organizations/{organization.organization_id}"
            f"/review-queues/{queue.review_queue_id}/review-cases"
        )

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
    # Filled when the lifespan runs, on the serving thread. A one-element list
    # rather than a nonlocal, because the lifespan is defined before the
    # fixture object it has to reach exists.
    counters_holder: list[CountingSessionService | None] = [None]
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
            # The application sees the counting wrapper; login keeps the real
            # service, so the counters measure the guard chain alone.
            app.state.session_service = counters_holder[0] = CountingSessionService(server_sessions)
            app.state.login_service = LoginService(
                authentication=AuthenticationService(
                    users=server_users,
                    credentials=SqliteCredentialRepository(server_database),
                    hasher=hasher,
                    clock=clock,
                ),
                sessions=server_sessions,
            )
            # Wired exactly as ``production_lifespan`` wires it: no queue is
            # resolved, and nothing knows which one a request will want.
            app.state.queue_binder = SqliteReviewQueueBinder(server_database)
            app.state.tenant_authorization = TenantAuthorizationService(server_users)
            yield
        finally:
            app.state.session_service = None
            app.state.login_service = None
            app.state.queue_binder = None
            app.state.tenant_authorization = None
            server_database.close()

    # The control connection. Created first so the schema exists before the
    # server opens its own, and used by the test for everything the server is
    # not doing.
    control = open_review_database(persistence, clock=clock)
    control_users = SqliteTenantRepository(control)
    app = create_app(auth_config=config, lifespan=lifespan)
    try:
        with TestClient(app) as client:
            assert counters_holder[0] is not None, "the lifespan did not run"
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
                counters=counters_holder[0],
            )
    finally:
        control.close()


def disable(auth: AuthFixture, user: User) -> None:
    """Switch an account off the way an operator eventually will."""
    auth.database.connect().execute(
        "UPDATE users SET status = ? WHERE user_id = ?",
        (UserStatus.DISABLED.value, user.user_id),
    )
