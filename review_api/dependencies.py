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
a version this build does not understand. Because that happens during startup,
such a process refuses to start rather than answering requests about a queue it
cannot read -- which is also why ``GET /health`` does not need to ask about
storage.

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

from review_application import ReviewCaseRepository, ReviewQueueService
from review_persistence import load_review_persistence_config
from review_persistence.sqlite import SqliteReviewCaseRepository, open_review_database

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
    database = open_review_database(config)
    try:
        repository = SqliteReviewCaseRepository(database)
        app.state.repository = repository
        app.state.service = ReviewQueueService(repository)
        yield
    finally:
        # Cleared before closing so a shutdown that races a request cannot hand
        # out a repository over a connection that is already gone.
        app.state.repository = None
        app.state.service = None
        database.close()


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


def get_service(request: Request) -> ReviewQueueService:
    """The Sprint 10 application service: the only supported way to resolve a case."""
    return _require_wired(request.app.state.service, "review queue service")
