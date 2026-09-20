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

The repository this module builds is bound to one review queue, resolved once
at startup by ``resolve_sole_review_queue``. That resolution is explicitly
transitional and is documented as such on the function; it exists because these
routes still have no authenticated caller to resolve a tenant from.

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
from review_application import ReviewCaseRepository, ReviewQueue, ReviewQueueService
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
        repository = SqliteReviewCaseRepository(
            database,
            review_queue_id=resolve_sole_review_queue(database).review_queue_id,
        )
        app.state.repository = repository
        app.state.service = ReviewQueueService(repository)
        app.state.auth_config = auth_config
        _wire_authentication(app, database)
        yield
    finally:
        # Cleared before closing so a shutdown that races a request cannot hand
        # out a repository over a connection that is already gone.
        app.state.repository = None
        app.state.service = None
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


def resolve_sole_review_queue(database: ReviewDatabase) -> ReviewQueue:
    """Find the one queue this unauthenticated build is allowed to serve.

    TRANSITIONAL. This function exists only because the HTTP surface has no
    authenticated caller yet, and it is deleted in the phase that adds one.
    Its replacement is already decided: a request's queue will be resolved from
    a verified session and organization membership, through a tenant scope,
    with the organization and queue named explicitly in the URL. Nothing in
    that design needs an installation-wide lookup, so this is the whole of the
    temporary code and there is exactly one line to remove.

    Three properties make it safe to ship in the meantime.

    It **creates nothing**. There is no default organization, no default queue,
    and no fallback: it reads what an operator already created and refuses if
    that is not exactly one thing. A build that invented a tenant to keep
    itself running would be a hidden production tenant, which is precisely what
    this must not become.

    It **takes nothing from a caller**. The queue is discovered once, at
    startup, from storage. No header, no path, no query parameter, and no
    request body influences it, so the current routes cannot be steered at a
    tenant even in principle.

    It **refuses ambiguity**. More than one queue means the installation has
    grown past what an API with no caller identity can serve, and the honest
    answer is to fail to start rather than to pick one. Zero means the operator
    has not bootstrapped yet. Both raise here, during startup, so the process
    never begins answering requests about a queue it cannot name.
    """
    queues = SqliteTenantRepository(database).list_all_review_queues()
    if not queues:
        raise RuntimeError(
            "No review queue is registered in this database. Create an organization "
            "and register a workflow before serving; this build will not invent one."
        )
    if len(queues) > 1:
        raise RuntimeError(
            f"This database holds {len(queues)} review queues, and this build has no "
            "authenticated caller that could choose between them. Refusing to serve one "
            "arbitrarily."
        )
    return queues[0]


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


def get_repository(request: Request) -> ReviewCaseRepository:
    """The queue's storage, typed as the Protocol and never as the SQLite class."""
    return _require_wired(request.app.state.repository, "review case repository")


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


def get_service(request: Request) -> ReviewQueueService:
    """The Sprint 10 application service: the only supported way to resolve a case."""
    return _require_wired(request.app.state.service, "review queue service")
