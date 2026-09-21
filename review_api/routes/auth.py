"""Four operations over the Phase C authentication core, and one cookie.

These routes decide nothing. They read a request, call a service that already
owns the rule, and project the result into an explicit model. No password is
compared here, no token is generated here, no expiry is computed here, and no
session row is inspected here -- all of that lives in ``identity`` and was
proven against a deterministic clock before any of it was reachable over a
socket.

What this layer genuinely owns is the cookie and the collapse.

**The cookie** is the only place the raw session token ever leaves the server.
It is never in a response body, never in a header this API reads back, and
never logged. Everything about it is set explicitly -- ``HttpOnly`` so script
cannot read it, ``SameSite=Strict`` so a cross-site request does not carry it,
``Secure`` unless an operator explicitly opted into the loopback-only insecure
mode, ``Path=/`` and no ``Domain`` so its scope is exactly this origin, and no
``Max-Age`` or ``Expires`` so it dies with the browser session.

**The collapse** is turning six distinct internal failures into one public
answer. A caller learns that authentication failed and nothing else: not
whether the account exists, not whether it is disabled, not whether the session
was revoked rather than expired, and not which of the two expiry bounds ended
it.

These are not the only authenticated endpoints any more. The tenant-scoped
review routes require the same principal dependency and then go further, asking
``review_api.tenancy`` whether that principal may reach the organization and
queue their URL names. Nothing in this module makes such a decision: these
routes establish identity, and identity alone grants access to nothing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from identity.authentication import AuthenticatedUser
from identity.errors import AuthenticationFailedError, SessionError
from identity.login import LoginService
from identity.session_service import SessionService
from review_api.auth_config import AuthHttpConfig
from review_api.auth_models import LoginRequest, SessionEnvelope, to_session_envelope
from review_api.dependencies import get_auth_config, get_login_service, get_session_service
from review_api.errors import UnauthenticatedError
from review_api.security import (
    AuthenticatedPrincipal,
    PrincipalDep,
    read_session_cookie,
    require_trusted_origin,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

AuthConfigDep = Annotated[AuthHttpConfig, Depends(get_auth_config)]
LoginServiceDep = Annotated[LoginService, Depends(get_login_service)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]

# ``PrincipalDep`` is imported rather than redeclared. Two annotations naming
# the same dependency would work -- FastAPI caches by the callable -- but one
# definition means "this route requires a session, and renews it" has a single
# spelling that the tenant routes share.

# Declared as a route dependency rather than called in the body, so FastAPI
# resolves it before the endpoint runs. A forged cross-site request therefore
# costs no password verification, renews no session and revokes nothing.
TrustedOrigin = Depends(require_trusted_origin)


def _set_session_cookie(response: Response, config: AuthHttpConfig, raw_token: str) -> None:
    """Hand the browser its bearer token, with every attribute stated.

    ``max_age`` and ``expires`` are both omitted, which makes this a browser-
    session cookie: it is gone when the browser closes. That is deliberate even
    though the server-side session may live up to eight hours. A persistent
    cookie would leave a usable credential on disk after the person walked
    away, and the server's own bounds -- 60 minutes idle, 8 hours absolute --
    are the authority on validity either way. Persisting it across browser
    restarts is what "remember me" means, and that is explicitly not this.

    No ``Domain``. Omitting it scopes the cookie to exactly the origin that set
    it; naming one would widen it to every subdomain, which is a larger blast
    radius than anything here needs.
    """
    response.set_cookie(
        key=config.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="strict",
        secure=config.session_cookie_secure,
        path="/",
    )


def _clear_session_cookie(response: Response, config: AuthHttpConfig) -> None:
    """Delete the cookie using the same scope that set it.

    A browser matches a deletion by name, path and domain. Clearing with a
    different path would leave the original cookie in place while appearing to
    succeed, so these attributes deliberately mirror ``_set_session_cookie``.
    """
    response.delete_cookie(
        key=config.session_cookie_name,
        httponly=True,
        samesite="strict",
        secure=config.session_cookie_secure,
        path="/",
    )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    dependencies=[TrustedOrigin],
    summary="Exchange credentials for a session",
)
async def login(
    body: LoginRequest,
    response: Response,
    config: AuthConfigDep,
    logins: LoginServiceDep,
    sessions: SessionServiceDep,
) -> SessionEnvelope:
    """Verify a password and start a session.

    The whole of the decision is ``LoginService.login``: it normalizes the
    handle with the Phase B rule, verifies the password against the stored
    Argon2id credential, performs a dummy verification when there is nothing to
    verify against, and only then creates a session. Nothing is written until
    the password has been verified, so a failed attempt leaves no session row
    and no record that the handle was tried against a real account.

    ``200`` rather than ``201``. A session is not a resource this API publishes
    at a new address -- there is no URL that identifies one, deliberately,
    because such a URL would be a credential.

    Every failure answers ``401`` with one body. An unknown handle, a wrong
    password, a disabled account and a user with no credential are one
    ``AuthenticationFailedError`` by the time they arrive here, and this route
    does not learn which. A storage failure is *not* caught: it reaches the
    catch-all as a 500, because a database that cannot be read has not rejected
    anyone's credentials and reporting it as one would send an operator to
    investigate the wrong thing.

    Membership is never consulted. A user who belongs to no organization logs
    in successfully and finds nothing to read -- an authorization outcome,
    decided later, by code that can see the membership table.
    """
    try:
        result = logins.login(body.email, body.password)
    except AuthenticationFailedError as exc:
        raise UnauthenticatedError("invalid credentials") from exc

    _set_session_cookie(response, config, result.created.raw_token)
    return to_session_envelope(result.user, result.created.session, sessions.policy)


@router.get(
    "/session",
    status_code=status.HTTP_200_OK,
    summary="Read the current session",
)
async def read_session(
    principal: PrincipalDep,
    sessions: SessionServiceDep,
) -> SessionEnvelope:
    """Who is signed in, and how long this session has left.

    This is an authenticated request, so it counts as activity: the principal
    dependency renewed the idle window once on the way in, and the session
    reported here is the state *after* that renewal. Returning the pre-touch
    timestamps would tell a client its session expires earlier than the server
    now believes, and a frontend timer built on that would warn too soon.

    No ``Origin`` requirement: a GET changes nothing, and the same-origin
    policy already keeps a cross-site caller from reading the response.

    No organization, queue or role. A later phase will answer what this user
    may reach, by reading current membership at that moment rather than
    anything captured here.
    """
    return to_session_envelope(
        _authenticated_user(principal),
        principal.session,
        sessions.policy,
    )


@router.post(
    "/session/continue",
    status_code=status.HTTP_200_OK,
    dependencies=[TrustedOrigin],
    summary="Extend the idle window",
)
async def continue_session(
    principal: PrincipalDep,
    sessions: SessionServiceDep,
) -> SessionEnvelope:
    """The explicit "I am still here", for the idle warning a frontend will show.

    Exactly one renewal happens, and it is the one the principal dependency
    already performed -- this body does not touch again. Two renewals would be
    harmless in effect but would make "one request, one activity record" false,
    and the difference between one and two is exactly what the activity tests
    pin.

    It creates no session, rotates no token, changes no user and moves no
    absolute expiry. A session that has already hit either bound is refused
    with the same generic ``401`` as any other unusable session: continuing is
    renewal, not resurrection.
    """
    return to_session_envelope(
        _authenticated_user(principal),
        principal.session,
        sessions.policy,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    dependencies=[TrustedOrigin],
    summary="End the current session",
)
async def logout(
    response: Response,
    config: AuthConfigDep,
    sessions: SessionServiceDep,
    request_token: Annotated[str | None, Depends(read_session_cookie)],
) -> None:
    """Revoke the session if there is one, and clear the cookie either way.

    Deliberately **not** using the principal dependency. That dependency
    renews activity, and touching a session on the way to ending it would be
    absurd; it also raises on an expired or revoked session, which would turn
    "log me out" into a 401 for exactly the people who most want the cookie
    gone.

    So logout is idempotent and says nothing. A valid session is revoked; a
    missing, unknown, expired or already-revoked cookie is simply cleared. All
    of them answer ``200``. Reporting "there was no session" would tell a
    caller whether the cookie they hold is live -- an oracle available without
    any credential at all.

    The origin check still applies, because logging someone out is a state
    change and a forced logout is a real, if minor, cross-site attack.
    """
    if request_token is not None:
        try:
            sessions.revoke_session(request_token)
        except SessionError:
            # Unknown, malformed or already revoked. Nothing to end, and
            # nothing to tell the caller about it.
            pass

    _clear_session_cookie(response, config)


def _authenticated_user(principal: AuthenticatedPrincipal) -> AuthenticatedUser:
    """Re-wrap the principal as the identity the response projection expects.

    A small adapter rather than passing the principal itself: the projection is
    shared with the login route, which has an ``AuthenticatedUser`` straight
    from the authentication service and no principal at all.
    """
    return AuthenticatedUser(
        user_id=principal.user_id,
        display_name=principal.display_name,
    )
