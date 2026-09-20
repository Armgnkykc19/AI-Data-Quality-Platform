"""The public error envelope, and the rule that keeps it safe.

Every error this API returns has one shape::

    {"error": {"code": "...", "message": "...", "details": null}}

``code`` is an API-owned token from :class:`ErrorCode`: stable across releases
and safe for a client to branch on, which a human-readable sentence is not.
``message`` is a static sentence owned by this module. ``details`` is either
``null`` or a structure this module built field by field. The key is always
present, so a client parsing the envelope never has to handle two shapes.

One rule produces all of that: **no exception text becomes response content.**

Not domain messages, not persistence messages, not Pydantic's rendering of a
value the client submitted. The exception messages in this project are richly
informative on purpose -- they name entity-resolution config paths, SQL tables,
schema versions, and stored review state -- and every one of those describes
the deployment to whoever can reach the port. This API has no authentication,
so that reader is not necessarily a reviewer.

The temptation is to forward the messages that look harmless today. The reason
not to is that the boundary is what has to hold, not the current wording: a
later message gains a path, and nothing fails. Operators lose nothing, because
the full exception is logged server-side where it is reachable and the response
is not.

Handlers are registered only for conditions a published endpoint can actually
produce. The read endpoints raise not-found, storage and integrity errors, so
those are mapped; conflict, invalid-transition, authorization and contradiction
errors belong to the resolution endpoint and arrive with it. A mapping no route
can reach is a branch no test exercises, and it drifts away from the endpoint it
was written for.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from human_review.errors import (
    HumanReviewAuthorizationContextError,
    HumanReviewAuthorizationError,
    HumanReviewContradictionError,
    InvalidReviewTransitionError,
)
from review_api.models import ApiResponseModel
from review_application import (
    PersistedCaseIntegrityError,
    ReviewAuthorizationConfigError,
    ReviewCaseNotFoundError,
    ReviewConflictError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
    ReviewSchemaVersionError,
    SemanticSuggestionIntegrityError,
)
from review_application.errors import ReviewWorkflowContextMissingError

logger = logging.getLogger(__name__)

# Written out rather than imported from starlette.status: the 422 constant was
# renamed (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT) and the old spelling
# now warns, so importing either one couples this module to a narrower starlette
# range than the declared dependency allows.
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_UNPROCESSABLE_CONTENT = 422
HTTP_CONFLICT = 409
HTTP_INTERNAL_SERVER_ERROR = 500
HTTP_SERVICE_UNAVAILABLE = 503


class ErrorCode(StrEnum):
    """Stable tokens a client may branch on.

    Owned by the API, not derived from any exception class name, so renaming an
    internal error never changes the public contract.
    """

    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    REVIEW_CASE_NOT_FOUND = "REVIEW_CASE_NOT_FOUND"
    REVIEW_STORAGE_UNAVAILABLE = "REVIEW_STORAGE_UNAVAILABLE"
    REVIEW_STORAGE_CORRUPT = "REVIEW_STORAGE_CORRUPT"
    REVIEW_CASE_VERSION_CONFLICT = "REVIEW_CASE_VERSION_CONFLICT"
    REVIEW_CASE_NOT_PENDING = "REVIEW_CASE_NOT_PENDING"
    HUMAN_REVIEW_CONTRADICTION = "HUMAN_REVIEW_CONTRADICTION"
    MATCH_NOT_AUTHORIZED = "MATCH_NOT_AUTHORIZED"
    AUTHORIZATION_CONTEXT_UNAVAILABLE = "AUTHORIZATION_CONTEXT_UNAVAILABLE"
    AUTHORIZATION_CONFIG_UNAVAILABLE = "AUTHORIZATION_CONFIG_UNAVAILABLE"
    REVIEW_QUEUE_NOT_READY = "REVIEW_QUEUE_NOT_READY"


# Static public messages. Deliberately vague where vagueness is the point: an
# unexpected failure tells the client nothing it could act on, because anything
# actionable would also be something an unauthenticated caller could learn.
PUBLIC_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "The request was not valid.",
    # One sentence for every way authentication can fail, because the
    # caller must not be able to tell them apart. See UnauthenticatedError.
    ErrorCode.UNAUTHENTICATED: "Authentication is required or the credentials are invalid.",
    ErrorCode.FORBIDDEN: "The request was refused.",
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.METHOD_NOT_ALLOWED: "The HTTP method is not allowed for this resource.",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred.",
    ErrorCode.REVIEW_CASE_NOT_FOUND: "No review case exists with that identifier.",
    ErrorCode.REVIEW_STORAGE_UNAVAILABLE: "The review queue is currently unavailable.",
    ErrorCode.REVIEW_STORAGE_CORRUPT: "The review queue returned inconsistent stored state.",
    ErrorCode.REVIEW_CASE_VERSION_CONFLICT: (
        "The review case changed after it was loaded. Reload it before deciding again."
    ),
    ErrorCode.REVIEW_CASE_NOT_PENDING: (
        "The review case has already been resolved and cannot be resolved again."
    ),
    ErrorCode.HUMAN_REVIEW_CONTRADICTION: (
        "The decision contradicts a review decision already recorded for these records."
    ),
    ErrorCode.MATCH_NOT_AUTHORIZED: (
        "The requested match is not permitted by the review safety rules."
    ),
    ErrorCode.AUTHORIZATION_CONTEXT_UNAVAILABLE: (
        "The server cannot authorize a match for this queue right now."
    ),
    ErrorCode.AUTHORIZATION_CONFIG_UNAVAILABLE: (
        "The server cannot authorize a match for this queue right now."
    ),
    ErrorCode.REVIEW_QUEUE_NOT_READY: "The review queue is not ready to accept decisions.",
}

# Domain and application errors a read endpoint can actually raise, and the
# public answer for each. Nothing speculative is listed: a mapping no route can
# reach is a branch no test can exercise, and it drifts away from the endpoint
# it was written for. Phase C adds conflict, invalid-transition, authorization
# and contradiction mappings alongside the resolution endpoint that raises them.
#
# ``ReviewApplicationError`` itself is deliberately not mapped. Catching the
# base class would silently absorb every future error into whichever code sat
# on the parent, which is exactly how a refused MATCH ends up reported as a
# storage failure.
#
# Starlette resolves a handler by walking ``type(exc).__mro__``, so the most
# specific registered class always wins regardless of registration order.
# ``ReviewSchemaVersionError`` is a ``ReviewPersistenceError`` and both answer
# 503, so that pair agrees either way; the integrity errors sit on a separate
# branch of the hierarchy and cannot be captured by the persistence entry.
DOMAIN_ERROR_MAPPINGS: tuple[tuple[type[Exception], int, ErrorCode], ...] = (
    # The one routine client-visible outcome here: an id that does not exist.
    (ReviewCaseNotFoundError, HTTP_NOT_FOUND, ErrorCode.REVIEW_CASE_NOT_FOUND),
    # Stored state contradicts itself. Not the caller's fault and not
    # retryable, so 500 rather than 503.
    (PersistedCaseIntegrityError, HTTP_INTERNAL_SERVER_ERROR, ErrorCode.REVIEW_STORAGE_CORRUPT),
    (ReviewEventIntegrityError, HTTP_INTERNAL_SERVER_ERROR, ErrorCode.REVIEW_STORAGE_CORRUPT),
    (
        SemanticSuggestionIntegrityError,
        HTTP_INTERNAL_SERVER_ERROR,
        ErrorCode.REVIEW_STORAGE_CORRUPT,
    ),
    # Storage itself is unreachable or speaks a schema this build does not
    # know. An operator can fix both, so a retry may succeed: 503.
    (ReviewSchemaVersionError, HTTP_SERVICE_UNAVAILABLE, ErrorCode.REVIEW_STORAGE_UNAVAILABLE),
    (ReviewPersistenceError, HTTP_SERVICE_UNAVAILABLE, ErrorCode.REVIEW_STORAGE_UNAVAILABLE),
    # ---- reachable only through the resolution endpoint ---------------------
    #
    # 409: the stored review state conflicts with the requested transition.
    # The case is terminal, or the decision contradicts a decision a human has
    # already recorded. Sprint 08 reaches the contradiction verdict by union-find
    # across every recorded NO_MATCH, so it is a statement about the queue's
    # current state, not about how the request was written -- which is what
    # separates it from the 422 below.
    (InvalidReviewTransitionError, HTTP_CONFLICT, ErrorCode.REVIEW_CASE_NOT_PENDING),
    (HumanReviewContradictionError, HTTP_CONFLICT, ErrorCode.HUMAN_REVIEW_CONTRADICTION),
    # 422: the request was well formed and the match was evaluated and refused.
    # Sprint 08 found a severe identity conflict inside the component the merge
    # would create. Not 401 or 403 -- nothing here concerns who is calling.
    (HumanReviewAuthorizationError, HTTP_UNPROCESSABLE_CONTENT, ErrorCode.MATCH_NOT_AUTHORIZED),
    # 503: the server lacks the trusted material to judge a MATCH safely, so no
    # verdict was reached at all. Reporting that as a refusal would tell a
    # reviewer their merge is unsafe when nothing established that.
    (
        HumanReviewAuthorizationContextError,
        HTTP_SERVICE_UNAVAILABLE,
        ErrorCode.AUTHORIZATION_CONTEXT_UNAVAILABLE,
    ),
    (
        ReviewAuthorizationConfigError,
        HTTP_SERVICE_UNAVAILABLE,
        ErrorCode.AUTHORIZATION_CONFIG_UNAVAILABLE,
    ),
    # 503: the queue holds cases but no stored authorization context, so no
    # decision of any kind can be applied. Newly reachable in this phase --
    # ``resolve_case`` opens with ``load_workflow_bundle``, which the read
    # endpoints never call.
    (
        ReviewWorkflowContextMissingError,
        HTTP_SERVICE_UNAVAILABLE,
        ErrorCode.REVIEW_QUEUE_NOT_READY,
    ),
)

# Only statuses Phase A can actually produce. Starlette raises 404 for an
# unmatched path and 405 when a path matches but the method does not.
_STATUS_ERROR_CODES: dict[int, ErrorCode] = {
    HTTP_NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTP_METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
}


class UnauthenticatedError(Exception):
    """The request carried no usable authenticated identity.

    Owned by this layer rather than imported, because it is the *collapse
    point*: a missing cookie, an unknown token, a malformed token, a revoked
    session, an idle-expired session, an absolutely-expired session and a
    session whose owner has since been disabled all become this one type, with
    one public message.

    Each of those is a distinct, typed error inside ``identity`` and stays
    distinct there -- ``SessionExpiredError`` even carries whether idleness or
    the absolute bound ended it. None of that reaches a caller. "Your session
    expired" versus "that session was revoked" tells someone holding a stolen
    token which of the two happened, and "no such account" versus "wrong
    password" tells a prober which addresses are registered.

    The ``reason`` argument is for the server log only and never for the
    response.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UntrustedOriginError(Exception):
    """A state-changing request came from an origin that is not configured.

    Raised before any authentication work, session mutation or revocation, so
    a forged cross-site request cannot cost a password verification, renew a
    session, or end one.
    """


class ApiError(ApiResponseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(ApiResponseModel):
    error: ApiError


def error_response(
    code: ErrorCode,
    *,
    status_code: int,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the one response shape this API emits for failures.

    The message is looked up rather than passed in, so no call site can supply
    text that came from an exception.
    """
    payload = ErrorResponse(
        error=ApiError(code=code, message=PUBLIC_MESSAGES[code], details=details)
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(), headers=headers)


def sanitized_validation_details(exc: RequestValidationError) -> dict[str, Any]:
    """Structural validation feedback only: which field, and which rule.

    Pydantic reports each failure with ``loc``, ``type``, ``msg``, ``input``,
    sometimes ``ctx``, and a documentation ``url``. Exactly two survive:
    ``loc`` becomes ``location`` and ``type`` is kept verbatim. Together they
    tell a client which field to fix and which constraint it violated, which is
    everything a client can act on.

    ``msg`` is dropped, and that is the deliberate part. Pydantic's built-in
    wording is harmless -- it describes the expected type or the permitted enum
    values -- so retaining it looks safe today. But ``msg`` is not an API-owned
    string: it is whatever produced the failure. A custom field validator that
    raises ``ValueError(f"Unknown reviewer {value}")`` has its text lifted
    straight into ``msg``, and the submitted value travels with it. Stripping
    ``input`` while keeping ``msg`` would leave that door open and look closed.

    So the rule is the same one the module docstring states, applied here
    without an exception: no prose this API did not write becomes response
    content. If friendly messages are wanted later they belong in a static
    mapping keyed by ``type``, owned here, where no validator can reach them.

    ``input`` is the submitted value itself, ``ctx`` can repeat it, and ``url``
    points at external documentation; none of the three reach the client.
    """
    fields = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "unknown")),
        }
        for error in exc.errors()
    ]
    return {"fields": fields}


async def handle_validation_error(request: Request, exc: Exception) -> Response:
    """422 for a malformed request, with values stripped from the feedback."""
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered by type
        return await handle_unexpected_error(request, exc)
    return error_response(
        ErrorCode.INVALID_REQUEST,
        status_code=HTTP_UNPROCESSABLE_CONTENT,
        details=sanitized_validation_details(exc),
    )


async def handle_http_exception(request: Request, exc: Exception) -> Response:
    """Reshape Starlette's own 404/405 into the envelope.

    ``exc.detail`` is discarded rather than forwarded. Starlette's defaults are
    harmless, but a handler that forwards detail today forwards whatever a
    future ``HTTPException`` carries, and that is the habit this module exists
    to prevent.

    The ``Allow`` header is preserved on a 405 because it is part of the HTTP
    contract and says nothing about the deployment.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered by type
        return await handle_unexpected_error(request, exc)

    code = _STATUS_ERROR_CODES.get(exc.status_code)
    if code is None:
        # No Phase A route raises another status. Falling back to a generic
        # token keeps the envelope valid without inventing a public code for a
        # condition that cannot occur yet.
        code = (
            ErrorCode.INTERNAL_ERROR
            if exc.status_code >= HTTP_INTERNAL_SERVER_ERROR
            else ErrorCode.INVALID_REQUEST
        )
    headers = getattr(exc, "headers", None)
    return error_response(code, status_code=exc.status_code, headers=headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """500 with a static body; the real failure goes to the log.

    This is the catch-all that makes the no-exception-text rule hold even for
    failures nobody anticipated -- including domain and persistence errors that
    reach the HTTP layer before their endpoint has a dedicated mapping.
    """
    logger.exception(
        "Unhandled error serving %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return error_response(ErrorCode.INTERNAL_ERROR, status_code=HTTP_INTERNAL_SERVER_ERROR)


def make_domain_handler(
    status_code: int,
    code: ErrorCode,
) -> Callable[[Request, Exception], Awaitable[Response]]:
    """Build a handler that answers one exception type with one static body.

    The exception never reaches the response. It reaches the log, at a level
    that matches who has to act: a 404 is a normal client outcome, anything 500
    or above is ours.
    """

    async def handler(request: Request, exc: Exception) -> Response:
        if status_code >= HTTP_INTERNAL_SERVER_ERROR:
            logger.exception("Serving %s %s failed", request.method, request.url.path, exc_info=exc)
        else:
            logger.info("Refused %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return error_response(code, status_code=status_code)

    return handler


async def handle_review_conflict(request: Request, exc: Exception) -> Response:
    """409 for a reviewer who lost the race, echoing only their own input.

    ``details`` carries ``expected_version`` -- the value this client submitted
    and nothing else. It is safe by construction, and it lets a client that has
    several decisions in flight see which one was rejected.

    The stored version is deliberately *not* included. Fetching it would mean an
    extra storage read inside an error path, and the answer could be stale
    before the response was written -- a second racer could land between the
    conflict and the read. A client that needs the current version re-fetches
    the case, which is what it has to do anyway to decide again. The CAS
    predicate, the stored version and the SQL behind them stay server-side.
    """
    if not isinstance(exc, ReviewConflictError):  # pragma: no cover - registered by type
        return await handle_unexpected_error(request, exc)

    logger.info(
        "Refused %s %s: stale version %s",
        request.method,
        request.url.path,
        exc.expected_version,
    )
    return error_response(
        ErrorCode.REVIEW_CASE_VERSION_CONFLICT,
        status_code=HTTP_CONFLICT,
        details={"expected_version": exc.expected_version},
    )


async def handle_unauthenticated(request: Request, exc: Exception) -> Response:
    """401 with one body, whatever actually went wrong.

    Logged at ``info`` with the internal reason and no traceback: a failed
    login or an expired cookie is an ordinary outcome, not a server fault, and
    a stack trace per attempt would bury real failures. The reason reaches the
    log; the response never carries it.

    No ``WWW-Authenticate`` header. That belongs to Basic and Bearer header
    schemes; this is cookie authentication, and advertising a challenge a
    browser would answer with a native dialog would be wrong.
    """
    reason = getattr(exc, "reason", type(exc).__name__)
    logger.info("Unauthenticated %s %s: %s", request.method, request.url.path, reason)
    return error_response(ErrorCode.UNAUTHENTICATED, status_code=HTTP_UNAUTHORIZED)


async def handle_untrusted_origin(request: Request, exc: Exception) -> Response:
    """403 for a state-changing request from an origin that is not configured.

    The offending ``Origin`` is attacker-controlled, so it is neither echoed
    into the response nor written to the log; and the configured allow-list is
    never disclosed, because that would tell a forger exactly which origin to
    obtain. The response says only that the request was refused.
    """
    logger.info("Refused untrusted origin on %s %s", request.method, request.url.path)
    return error_response(ErrorCode.FORBIDDEN, status_code=HTTP_FORBIDDEN)


def register_error_handlers(app: FastAPI) -> None:
    """Install every handler this API answers with."""
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(UnauthenticatedError, handle_unauthenticated)
    app.add_exception_handler(UntrustedOriginError, handle_untrusted_origin)
    for exception_type, status_code, code in DOMAIN_ERROR_MAPPINGS:
        app.add_exception_handler(exception_type, make_domain_handler(status_code, code))
    # Registered on its own because it is the one mapping that returns details.
    app.add_exception_handler(ReviewConflictError, handle_review_conflict)
    # Registered last as the floor beneath everything above, including the
    # domain and persistence errors that have no mapping yet.
    app.add_exception_handler(Exception, handle_unexpected_error)
