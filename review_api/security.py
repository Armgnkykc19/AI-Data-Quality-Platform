"""The guard chain every tenant-scoped request passes through, in order.

Four steps, each a dependency, each answering one question::

    require_trusted_origin        may a state-changing request proceed at all?
    get_authenticated_principal   who is making it?
    require_capability(...)       may they reach this queue, and do this?
    scoped_repository / _service  the review layer bound to that one queue

They are dependencies rather than middleware for the reason the rest of this
API uses dependencies: a route's guarantees are visible in its signature, a
test can override one through ``app.dependency_overrides``, and adding a route
that forgot its guard is a diff a reviewer can see. Middleware would move all
four decisions into a path-prefix list somewhere else in the file.

Order is load-bearing and comes for free from where each is placed. FastAPI
resolves a route's dependencies before its body runs, so a rejected origin
costs no password verification, renews no session, and revokes nothing; and an
unauthorized caller never reaches storage, because the repository is only built
after the scope is proven.

**One session touch per request, and the structure is what guarantees it.**
Only ``get_authenticated_principal`` resolves and renews a session. Tenant
authorization consumes the ``AuthenticatedPrincipal`` it produced and never
looks at a cookie, and FastAPI caches a dependency's result within a request --
so a route that depends on both the scope and the scoped service still
authenticates once, authorizes once, and renews once.

Both are ``async def``, and that is not stylistic. FastAPI runs a *synchronous*
dependency in a threadpool worker, while every route in this API is ``async``
and runs on the event loop. ``ReviewDatabase`` holds one ``sqlite3``
connection, and such a connection is legal only on the thread that created it
-- so a synchronous dependency that touched the session store would raise
``ProgrammingError`` on its first query. Declaring them ``async`` keeps every
database call on the one thread the lifespan opened the connection on, which is
the same constraint ``review_api.dependencies`` documents for the routes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Path
from starlette.requests import Request

from identity.errors import (
    InactiveUserError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevokedError,
)
from identity.session_service import SessionService
from identity.sessions import Session
from review_api.auth_config import AuthHttpConfig
from review_api.dependencies import (
    get_auth_config,
    get_queue_binder,
    get_session_service,
    get_tenant_authorization,
)
from review_api.errors import UnauthenticatedError, UntrustedOriginError
from review_api.models import MAX_TENANT_ID_LENGTH
from review_api.tenancy import Capability, TenantScope
from review_application import ReviewCaseRepository, ReviewQueueService

__all__ = [
    "AuthenticatedPrincipal",
    "OrganizationIdPath",
    "ReadScopeDep",
    "ResolveScopeDep",
    "ReviewQueueIdPath",
    "ScopedRepositoryDep",
    "ScopedServiceDep",
    "get_authenticated_principal",
    "read_session_cookie",
    "require_capability",
    "require_trusted_origin",
]

# Bounds only, on both tenant path segments. The ``ORG-``/``RQ-`` prefixes are
# not restated here: ``identity.ids.assert_opaque_id`` already owns that rule,
# and a second copy in the transport layer would be a second thing to keep in
# step. A malformed id simply matches nothing in storage and becomes the same
# 404 as an id that was merely never created -- which is exactly the answer the
# leakage policy wants for both.
OrganizationIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_TENANT_ID_LENGTH,
        description="Opaque organization identifier.",
    ),
]
ReviewQueueIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_TENANT_ID_LENGTH,
        description="Opaque review queue identifier, owned by the organization above.",
    ),
]

# Methods that do not change state. An Origin check on these would buy nothing
# -- a cross-site GET cannot be prevented by refusing it, since the response is
# already unreadable to the attacker under the same-origin policy -- and would
# break ordinary navigation.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Who is making this request, and the session that says so.

    Identity only. There is no organization, no queue, no role and no
    capability here, and none may be added: this object answers *who*, and a
    later authorization dependency answers *what may they reach*, by reading
    current membership at that moment.

    That separation is what makes access changes take effect immediately. A
    principal carrying a role would carry it for as long as the session lived,
    so removing someone's access could take up to eight hours to mean anything.

    ``session`` is the state *after* this request's activity renewal, so a
    response built from it tells the client what the server now believes.
    ``repr=False`` on it keeps the token digest out of any rendering, matching
    the Phase C objects.
    """

    user_id: str
    display_name: str
    session_id: str
    session: Session = field(repr=False)


async def require_trusted_origin(request: Request) -> None:
    """Refuse a state-changing request that did not come from a known origin.

    This is the CSRF control, and it is deliberately not the only one --
    ``SameSite=Strict`` on the cookie means a cross-site request should not
    carry it in the first place. Two independent mechanisms, because
    ``SameSite`` is enforced by the browser and this is enforced by us, and a
    control you do not enforce yourself is a control you are trusting someone
    else to keep.

    **A missing ``Origin`` is refused**, not waved through. Every browser sends
    it on a cross-origin request and on any same-origin POST, so the header's
    absence means the caller is not the browser this API is built for. Treating
    absence as trustworthy is the standard way this check is bypassed: a
    forger who can suppress the header would otherwise face no check at all.
    The cost is that a hand-rolled ``curl`` call must pass ``-H Origin: ...``,
    which is a documented, deliberate friction rather than a hole.

    Matching is exact, against whole normalized origins. There is no prefix,
    suffix or wildcard comparison anywhere in this path -- see
    ``AuthHttpConfig.is_allowed_origin`` for why those fail.
    """
    if request.method.upper() in _SAFE_METHODS:
        return

    config = _auth_config(request)
    origin = request.headers.get("origin")
    if origin is None or not config.is_allowed_origin(origin):
        raise UntrustedOriginError("The request origin is not trusted.")


def read_session_cookie(request: Request) -> str | None:
    """The raw bearer token the browser sent, if it sent one."""
    return request.cookies.get(_auth_config(request).session_cookie_name)


async def get_authenticated_principal(request: Request) -> AuthenticatedPrincipal:
    """Resolve the session cookie into a verified identity, and record activity.

    Four steps, in this order: read the cookie, resolve it through the Phase C
    session service, renew activity, and return the post-renewal state.

    **Renewal happens here, once.** Phase C deliberately kept ``resolve`` and
    ``touch`` apart so that "which requests keep a session alive" is a decision
    this layer states rather than a hidden write inside a lookup. The decision
    is: a request that authenticates successfully counts as activity. That is
    what stops a reviewer who is working steadily from being logged out for not
    pressing a button, and it is why the explicit *Continue* endpoint is a
    convenience rather than the only way to stay signed in.

    What does **not** renew follows from the same placement. A request with no
    cookie, an unknown or malformed token, a revoked or expired session, or one
    whose owner has been disabled never reaches the renewal -- it raises first.
    A request refused by the origin check never reaches this dependency at all.
    And logout deliberately does not use this dependency, because touching a
    session on the way to revoking it would be absurd.

    Every failure becomes one ``UnauthenticatedError``. The distinctions --
    unknown, malformed, revoked, idle-expired, absolutely-expired, disabled
    owner -- are real and stay typed inside ``identity``; publishing them would
    tell whoever holds a token which of those it is.

    Renewal can never extend the absolute bound: ``touch_session`` caps the
    idle window at it, so an actively used session still ends eight hours after
    it began.
    """
    raw_token = read_session_cookie(request)
    if raw_token is None:
        raise UnauthenticatedError("no session cookie")

    sessions = _session_service(request)
    try:
        resolved = sessions.resolve_session(raw_token)
        # The one renewal. Its result is what the response reports, so a client
        # is never told an idle expiry the server has already moved past.
        session = sessions.touch_session(raw_token)
    except SessionNotFoundError as exc:
        raise UnauthenticatedError("unknown or malformed session token") from exc
    except SessionRevokedError as exc:
        raise UnauthenticatedError("session revoked") from exc
    except SessionExpiredError as exc:
        # exc.reason distinguishes idle from absolute. It reaches the log
        # through the handler and never the response.
        raise UnauthenticatedError(f"session expired ({exc.reason})") from exc
    except InactiveUserError as exc:
        raise UnauthenticatedError("session owner is not active") from exc

    return AuthenticatedPrincipal(
        user_id=resolved.user.user_id,
        display_name=resolved.user.display_name,
        session_id=session.session_id,
        session=session,
    )


PrincipalDep = Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)]


def require_capability(
    capability: Capability,
) -> Callable[..., Awaitable[TenantScope]]:
    """Build the dependency that proves a scope for one required capability.

    A factory rather than a parameterized dependency because the capability is
    a property of the *route*, fixed when the route is declared, and never
    something a request could influence. There is no header, query parameter or
    body field through which a caller could ask for a different one.

    The returned dependency declares the two tenant path parameters itself. That
    is what makes the scope inseparable from the URL: there is no spelling of
    these routes in which an organization or a queue could be defaulted, read
    from a cookie, inferred from the principal's sole membership, or taken from
    the body. If a segment is absent the route does not match at all.

    ``async def`` for the same reason the rest of this module is: a synchronous
    dependency runs in a threadpool worker, and the membership read below goes
    through the one ``sqlite3`` connection the lifespan opened on the serving
    thread.
    """

    async def dependency(
        request: Request,
        organization_id: OrganizationIdPath,
        review_queue_id: ReviewQueueIdPath,
        principal: PrincipalDep,
    ) -> TenantScope:
        return get_tenant_authorization(request).authorize(
            # The authenticated principal's id, never anything from the request
            # body or a header. This is also the value the resolution route
            # hands the domain as reviewer_id.
            user_id=principal.user_id,
            organization_id=organization_id,
            review_queue_id=review_queue_id,
            capability=capability,
        )

    return dependency


# Declared once each, at module scope, because FastAPI caches a dependency's
# result per request keyed by the callable. Calling ``require_capability`` again
# inside a route would produce a second function object, and the scope would be
# authorized twice for one request.
ReadScopeDep = Annotated[TenantScope, Depends(require_capability(Capability.READ_REVIEW_QUEUE))]
ResolveScopeDep = Annotated[
    TenantScope, Depends(require_capability(Capability.RESOLVE_REVIEW_CASE))
]


async def scoped_repository(request: Request, scope: ReadScopeDep) -> ReviewCaseRepository:
    """Review storage bound to the queue this request was just authorized for.

    The binding happens here and nowhere earlier. A repository built at startup
    would have to be built for *some* queue, and whichever one that was would be
    the queue every request saw.
    """
    return get_queue_binder(request).repository_for(scope.review_queue_id)


async def scoped_service(request: Request, scope: ResolveScopeDep) -> ReviewQueueService:
    """The Sprint 10 application service, bound to the authorized queue.

    Depends on the *resolve* scope rather than the read scope, so the service
    that can record a decision is unreachable without
    ``RESOLVE_REVIEW_CASE``. A VIEWER's request is refused before this runs.
    """
    return get_queue_binder(request).service_for(scope.review_queue_id)


ScopedRepositoryDep = Annotated[ReviewCaseRepository, Depends(scoped_repository)]
ScopedServiceDep = Annotated[ReviewQueueService, Depends(scoped_service)]


def _auth_config(request: Request) -> AuthHttpConfig:
    """The configured cookie and origin policy for this application.

    Read from application state rather than from the environment or a module
    global, so an application built for a test is configured by how it was
    built. There is no default reached at request time: an unwired application
    fails loudly rather than falling back to something permissive.
    """
    return get_auth_config(request)


def _session_service(request: Request) -> SessionService:
    return get_session_service(request)
