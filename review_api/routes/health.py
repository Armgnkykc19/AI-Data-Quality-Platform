"""Liveness, and deliberately nothing more.

``GET /health`` answers one question: is this process running and able to serve
HTTP. It answers no other, and the omissions are the design. No schema version,
no database path, no queue state, no build identifier -- each of those describes
the deployment to anyone who can reach the port, and this API has no
authentication to decide who that is.

It also does not touch storage, for two independent reasons.

A liveness probe that opens a database reports a storage outage as process
death, and an orchestrator responds by restarting a process that was working
fine. Liveness and queue usability are different questions and want different
answers.

And the natural way to ask about queue state -- ``workflow_context()`` -- is not
part of the ``ReviewCaseRepository`` Protocol. Calling it here would make the
HTTP layer reach through the abstraction into the concrete SQLite repository to
answer a question about liveness, which is the exact dependency the layering
rules exist to prevent.

Storage validity is a startup concern instead: ``production_lifespan`` opens and
validates the database, and a process that cannot do so fails to start rather
than reporting itself healthy. A readiness endpoint that separates "running"
from "queue usable" is a real thing to want, but it belongs with the endpoints
whose readiness it would describe.
"""

from __future__ import annotations

from fastapi import APIRouter

from review_api.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> HealthResponse:
    """Return ``{"status": "ok"}``.

    Declared ``async`` like every route in this API. It performs no I/O, so it
    would be correct either way, but a mixed sync/async surface invites the one
    mistake Sprint 11 cannot afford: a handler dispatched to a worker thread,
    where the shared ``sqlite3`` connection is not legal. See
    ``review_api.dependencies``.
    """
    return HealthResponse(status="ok")
