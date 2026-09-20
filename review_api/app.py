"""The application factory, and the boundary between building and wiring.

Two functions, because those are two different jobs.

``create_app`` builds an application: routers, error handlers, settings. It
opens nothing. Given no arguments it produces a storage-free app, which is
exactly right while ``GET /health`` is the only route, and is what lets a test
build an application without the production queue existing.

``create_production_app`` is the deployment entry point. It attaches the
lifespan from ``review_api.dependencies``, which opens the review database at
startup and closes it at shutdown.

Storage wiring is opt-in rather than defaulted on purpose. If ``create_app()``
quietly reached for the configured database, every test that built one would
touch ``storage/review_queue.db`` -- the real queue, holding real human
decisions -- and would do so without a single line saying so.

There is no module-level ``app = FastAPI()``. A module-level application is
constructed at import time, which makes import order part of the runtime
contract and gives tests a shared instance they cannot isolate.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI

from identity.login import LoginService
from identity.session_service import SessionService
from review_api.auth_config import AuthHttpConfig
from review_api.dependencies import production_lifespan
from review_api.errors import register_error_handlers
from review_api.routes import auth, health, review_cases
from review_application import ReviewCaseRepository, ReviewQueueService

API_TITLE = "AI Data Quality Platform Review API"
API_VERSION = "0.1.0"

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    *,
    repository: ReviewCaseRepository | None = None,
    service: ReviewQueueService | None = None,
    login_service: LoginService | None = None,
    session_service: SessionService | None = None,
    auth_config: AuthHttpConfig | None = None,
    lifespan: LifespanFactory | None = None,
) -> FastAPI:
    """Build an application. Opens no database and reads no configuration.

    ``repository`` and ``service`` are the test injection seam: pass either and
    the routes that need it in a later phase find it on ``app.state`` without a
    lifespan ever running. Both are typed as abstractions -- the Protocol and
    the Sprint 10 service -- so a fake satisfying the contract is as valid here
    as the SQLite implementation.

    ``lifespan`` is the production seam. It is a parameter rather than a
    hard-coded import so that the storage-free default stays the default.

    ``debug=False`` is explicit rather than inherited: with debug on, Starlette
    renders an unhandled exception as an HTML traceback and the catch-all
    handler in ``review_api.errors`` is bypassed. That single flag is the
    difference between a static 500 and a stack trace containing file paths and
    SQL.

    ``login_service``, ``session_service`` and ``auth_config`` are the same
    seam for the authentication routes. ``auth_config`` defaults to ``None``
    rather than to a permissive object: an application nobody configured must
    fail loudly at the first authenticated request, never quietly accept every
    origin or drop ``Secure`` from the cookie.

    No CORS middleware is installed, and Sprint 13 does not add one. CORS
    exists to *permit* cross-origin requests; the browser reaches this API
    same-origin through the Vite proxy, so there is no legitimate cross-origin
    request to allow -- only forged ones to refuse, which is what the Origin
    check in ``review_api.security`` does. Adding CORS to make a test pass
    would hand browser-mediated access to customer record data.
    """
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        debug=False,
        lifespan=lifespan,
    )
    # Always present, so a dependency reads None rather than raising
    # AttributeError on an application nobody wired.
    app.state.repository = repository
    app.state.service = service
    app.state.login_service = login_service
    app.state.session_service = session_service
    app.state.auth_config = auth_config

    register_error_handlers(app)
    app.include_router(health.router)
    # Authentication only. These routes establish *who* a caller is; nothing
    # here decides what they may reach, and the review routes below remain
    # untouched -- see the module docstring in ``routes.auth``.
    app.include_router(auth.router)
    # Read-only. The routes resolve nothing and write nothing; an application
    # built without a repository still serves /health and answers these with a
    # static 500 rather than inventing an empty queue.
    app.include_router(review_cases.router)
    return app


def create_production_app() -> FastAPI:
    """The deployment entry point: an application that owns a real review queue.

    Kept separate from ``create_app`` so that "serve the configured database" is
    something a caller asks for by name, never something it gets by omission.
    """
    return create_app(lifespan=production_lifespan)
