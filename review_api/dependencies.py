"""Production composition root.

This is the only module in ``review_api`` allowed to import
``review_persistence``. Routes depend on the ``ReviewCaseRepository`` Protocol
and on ``ReviewQueueService``; the decision that those are backed by SQLite is
made here and nowhere else, which is what keeps a storage detail from appearing
in a route module. ``tests/review_api/test_api_layering.py`` asserts it.

Storage is opened inside the lifespan rather than in ``create_app``, so
constructing an application -- in a test, in a tool, at import time -- never
creates ``storage/review_queue.db``, never writes a schema, and never validates
one. The database is opened when the server begins serving and closed when it
stops.

``open_review_database`` validates the stored schema version and fails closed on
a version this build does not understand -- including a pre-tenancy 1.0.0
database, which is refused with a migration-required error rather than upgraded.
Because that happens during startup, such a process refuses to start rather than
answering requests about a queue it cannot read -- which is also why
``GET /health`` does not need to ask about storage.

**No queue is resolved at startup.** Nothing here reads how many queues the
database holds, and the application serves as many as an operator created. A
request's queue is decided per request, by tenant authorization, from the
organization and queue the URL names and the membership storage holds --
``SqliteReviewQueueBinder`` below then constructs a repository bound to exactly
that queue. The transitional sole-queue resolution that stood here while the
routes had no authenticated caller is gone, and with it the constraint that an
installation may hold exactly one review queue.

A temporary runtime constraint, stated plainly because it shapes every route:
``ReviewDatabase`` holds a single ``sqlite3`` connection, and a ``sqlite3``
connection is legal on the thread that created it and no other. Sprint 11
therefore declares its routes ``async def`` and calls the synchronous service
inline on the event loop, which keeps every database call on that one thread.
Sprint 10's threading semantics are deliberately left untouched: no
``check_same_thread=False``, no lock, no executor, no pool.

The consequence is that requests serialize, and this is not a scalable runtime
design -- it is an accepted constraint for a local, low-concurrency reviewer
API. Sprint 14 owns the concurrency model. Until then, no call into the service
or the repository may be moved onto a worker thread.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from fastapi import FastAPI
from starlette.requests import Request

from identity.authentication import AuthenticationService
from identity.login import LoginService
from identity.passwords import Argon2idPasswordHasher
from identity.session_service import SessionService
from review_api.auth_config import AuthHttpConfig, load_auth_http_config
from review_api.tenancy import ReviewQueueBinder, TenantAuthorizationService
from review_application import ReviewCaseRepository, ReviewQueueService
from review_persistence import load_review_persistence_config
from review_persistence.sqlite import (
    ReviewDatabase,
    SqliteCredentialRepository,
    SqliteReviewCaseRepository,
    SqliteSessionRepository,
    SqliteTenantRepository,
    open_review_database,
)

_T = TypeVar("_T")


class SqliteReviewQueueBinder:
    """Builds a queue-scoped review layer for one already-authorized queue id.

    Satisfies the ``ReviewQueueBinder`` Protocol structurally. It is the only
    place that knows a review queue is a row in SQLite, and it is reached only
    after tenant authorization has proven the caller may address the queue whose
    id it is handed.

    **There is no current queue.** Every call takes the id explicitly and
    returns something bound to it; nothing is rebound, and no attribute holds
    "the" queue. Two requests naming different queues therefore cannot see each
    other's data even in principle -- the isolation is structural rather than a
    rule someone has to follow.

    ``service_for`` memoizes one ``ReviewQueueService`` per queue id, and that
    cache is not a convenience. ``ReviewQueueService`` holds the
    entity-resolution configs it has loaded, keyed by the stored path, because
    re-reading that file between two decisions could authorize the second
    against thresholds the first never saw. A service rebuilt per request would
    throw that away. The cache is keyed by the explicit queue id and never
    consulted without one, so it adds no ambient state; a service is stateless
    with respect to the queue's *contents*, since ``resolve_case`` builds a
    fresh ``ReviewWorkflow`` from storage on every call.
    """

    def __init__(self, database: ReviewDatabase) -> None:
        self._database = database
        self._services: dict[str, ReviewQueueService] = {}

    def repository_for(self, review_queue_id: str) -> ReviewCaseRepository:
        """A repository that can see this queue and, structurally, nothing else."""
        return SqliteReviewCaseRepository(self._database, review_queue_id=review_queue_id)

    def service_for(self, review_queue_id: str) -> ReviewQueueService:
        cached = self._services.get(review_queue_id)
        if cached is None:
            cached = ReviewQueueService(self.repository_for(review_queue_id))
            self._services[review_queue_id] = cached
        return cached


@asynccontextmanager
async def production_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the review database for exactly as long as the server serves.

    Configuration is read here, not from the environment and never from a
    request: ``load_review_persistence_config`` anchors a relative database path
    to the project root, so the queue does not move when the server is launched
    from another directory.
    """
    config = load_review_persistence_config()
    auth_config = load_auth_http_config()
    database = open_review_database(config)
    try:
        app.state.queue_binder = SqliteReviewQueueBinder(database)
        app.state.tenant_authorization = TenantAuthorizationService(
            SqliteTenantRepository(database)
        )
        app.state.auth_config = auth_config
        _wire_authentication(app, database)
        yield
    finally:
        # Cleared before closing so a shutdown that races a request cannot hand
        # out a binder over a connection that is already gone.
        app.state.queue_binder = None
        app.state.tenant_authorization = None
        app.state.login_service = None
        app.state.session_service = None
        database.close()


def _wire_authentication(app: FastAPI, database: ReviewDatabase) -> None:
    """Build the authentication services over the one open database.

    Every repository here shares the single ``ReviewDatabase`` the lifespan
    opened. That is not an optimisation: a ``sqlite3`` connection is legal only
    on the thread that created it, and a second connection to the same file
    would give a session write and a review write two different views of it.

    The hasher is constructed once, at startup, with no arguments -- so it uses
    argon2-cffi's own parameters rather than anything this project pinned. It
    is stateless and safe to share; building one per request would also rebuild
    the cached dummy verifier that the enumeration defence depends on being
    computed once.
    """
    users = SqliteTenantRepository(database)
    hasher = Argon2idPasswordHasher()
    sessions = SessionService(
        sessions=SqliteSessionRepository(database),
        users=users,
    )
    authentication = AuthenticationService(
        users=users,
        credentials=SqliteCredentialRepository(database),
        hasher=hasher,
    )
    app.state.session_service = sessions
    app.state.login_service = LoginService(authentication=authentication, sessions=sessions)


def configured_database_path() -> Path:
    """Where the production application would look for its queue.

    Reads the same configuration ``production_lifespan`` reads, and opens
    nothing. The official runner uses this to tell an operator that no queue has
    been registered yet, instead of starting a server that would create an empty
    database and answer every request with an empty list.

    It lives here because this module is the only part of ``review_api`` allowed
    to know where the queue is stored.
    """
    return load_review_persistence_config().database_path


def _require_wired(component: _T | None, name: str) -> _T:
    """Fail loudly when a route is reached on an application with no storage.

    An unwired application is a deployment fault, not a client error, so this
    raises rather than returning a partial answer. The catch-all handler turns
    it into a static 500; the detail below reaches the log, never the response.
    """
    if component is None:
        raise RuntimeError(
            f"No {name} is wired into this application. Serve with "
            "create_production_app(), or inject one through create_app()."
        )
    return component


def get_queue_binder(request: Request) -> ReviewQueueBinder:
    """The factory that binds review storage to one authorized queue.

    Typed as the Protocol and never as the SQLite class, so a route can only
    ask for a queue by id and can never reach a storage-only method.

    There is deliberately no ``get_repository`` beside this. An unscoped
    accessor would hand a route the whole queue surface without anything having
    decided *which* queue, which is precisely the bypass Phase E removes.
    """
    return _require_wired(request.app.state.queue_binder, "review queue binder")


def get_tenant_authorization(request: Request) -> TenantAuthorizationService:
    """The policy that decides whether a principal may reach a named queue.

    Wired once, at startup, over the authoritative tenant tables. Not a default
    reached at request time: an application nobody wired fails loudly rather
    than falling back to something that would let every request through.
    """
    return _require_wired(request.app.state.tenant_authorization, "tenant authorization service")


def get_auth_config(request: Request) -> AuthHttpConfig:
    """The cookie and origin policy this application was built with.

    Never a default reached at request time. An application nobody configured
    fails loudly rather than falling back to something permissive -- a fallback
    here would mean an unconfigured deployment silently accepting every origin
    or dropping ``Secure`` from the cookie.
    """
    return _require_wired(request.app.state.auth_config, "authentication configuration")


def get_login_service(request: Request) -> LoginService:
    """The Phase C login orchestration: verify a password, then start a session."""
    return _require_wired(request.app.state.login_service, "login service")


def get_session_service(request: Request) -> SessionService:
    """The Phase C session lifecycle: resolve, renew, revoke."""
    return _require_wired(request.app.state.session_service, "session service")
