"""The published authentication contract: one request model and two responses.

Explicit models, never a serialized domain object. That rule matters more here
than anywhere else in this API, because the objects behind these responses
carry live secrets: ``CreatedSession`` holds the raw bearer token and
``PasswordCredential`` holds an Argon2id verifier. A route that returned a
domain object directly -- or that grew a ``dict(**session.__dict__)`` -- would
publish them the moment someone added a field.

So the direction is inverted: nothing reaches a response unless it is named
here. What is deliberately absent from every response below:

* the raw session token -- it leaves the server **only** in ``Set-Cookie``,
* the stored token digest,
* the session id, which is an internal handle for revocation and audit,
* the user's email address, which the caller just supplied and nothing
  downstream needs,
* any password field,
* and organization, queue, role or membership -- a session establishes
  identity, and authorization is read fresh at the moment of each request.

The last one is not an oversight to be corrected later. Publishing a role here
would invite a client to cache it, and a cached role is a role that stays in
force after an operator removes it.
"""

from __future__ import annotations

from pydantic import Field

from identity.authentication import AuthenticatedUser
from identity.sessions import Session, SessionPolicy
from review_api.models import ApiRequestModel, ApiResponseModel

__all__ = [
    "AuthenticatedUserRead",
    "LoginRequest",
    "SessionEnvelope",
    "SessionRead",
    "to_session_envelope",
]

# Transport hygiene, not policy. The password policy lives in Phase C and is
# applied by the authentication core; these bounds only stop an unbounded body
# from reaching the hasher. The maximum is generous enough that no password the
# policy would accept is refused here first.
MAX_EMAIL_LENGTH = 320
MAX_PASSWORD_LENGTH = 1024


class LoginRequest(ApiRequestModel):
    """Credentials as a caller submits them.

    ``extra="forbid"`` is inherited from ``ApiRequestModel`` and is a security
    control here: a caller must not be able to smuggle an extra field -- a
    ``user_id``, an ``organization``, a ``role`` -- into the one request that
    establishes identity.

    Both fields are ``strict`` so Pydantic does not coerce. Without it the JSON
    number ``123456789012`` would become the password ``"123456789012"``, and a
    caller and the server would disagree about what was actually submitted.

    Nothing here normalizes. The email is normalized once, by the Phase B rule,
    inside ``AuthenticationService``; the password is never normalized at all,
    because any transformation would have to be reproduced identically forever
    against hashes written before it existed.
    """

    email: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_EMAIL_LENGTH,
        description="Login handle. Normalized server-side for lookup.",
    )
    password: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_PASSWORD_LENGTH,
        description="Submitted verbatim to the password verifier.",
    )


class AuthenticatedUserRead(ApiResponseModel):
    """Who is authenticated. Two fields, and neither is a secret or an address."""

    user_id: str
    display_name: str


class SessionRead(ApiResponseModel):
    """When this session started, when it was last used, and when it ends.

    The timing policy travels with the session rather than being a second
    endpoint or a frontend constant. A browser that computed its own idle
    warning from a hard-coded 55 minutes could drift from the expiry the server
    actually applies and warn about a session that had already ended.

    ``warning_after_seconds`` is advisory and the server never enforces it;
    the two expiry timestamps are authoritative.
    """

    created_at_utc: str
    last_activity_at_utc: str
    idle_expires_at_utc: str
    absolute_expires_at_utc: str
    idle_timeout_seconds: int
    warning_after_seconds: int
    absolute_timeout_seconds: int


class SessionEnvelope(ApiResponseModel):
    """The body every authentication endpoint returns.

    One shape for login, for reading the current session, and for continuing
    it, so a client parses one thing. The cookie is what changes between them,
    not the schema.
    """

    user: AuthenticatedUserRead
    session: SessionRead


def to_session_envelope(
    user: AuthenticatedUser,
    session: Session,
    policy: SessionPolicy,
) -> SessionEnvelope:
    """Project a verified identity and its session onto the published shape.

    Pure: it reads three objects and builds a model. It performs no I/O and
    computes nothing a caller acts on beyond restating the policy in seconds,
    which is the unit a browser timer needs.

    ``session`` must be the state *after* any activity renewal this request
    performed, or the response would tell a client its session expires earlier
    than the server now believes.
    """
    return SessionEnvelope(
        user=AuthenticatedUserRead(user_id=user.user_id, display_name=user.display_name),
        session=SessionRead(
            created_at_utc=session.created_at_utc,
            last_activity_at_utc=session.last_activity_at_utc,
            idle_expires_at_utc=session.idle_expires_at_utc,
            absolute_expires_at_utc=session.absolute_expires_at_utc,
            idle_timeout_seconds=int(policy.idle_timeout.total_seconds()),
            warning_after_seconds=int(policy.warning_after.total_seconds()),
            absolute_timeout_seconds=int(policy.absolute_timeout.total_seconds()),
        ),
    )
