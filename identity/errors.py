"""Typed errors for identity and tenant records.

Kept separate from ``review_application.errors`` on purpose. A caller must be
able to tell "this organization already exists" from "the review queue refused
a decision": the two are produced by different layers, mean different things to
an operator, and will eventually map to different HTTP answers.

Nothing here is an ``Exception`` subclass that persistence invents on its own.
The SQLite tenant repository raises these, exactly as the review repository
raises ``review_application`` errors, so storage never has to define a parallel
error vocabulary of its own.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "AuthenticationFailedError",
    "CredentialNotFoundError",
    "DuplicateIdentityError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityValidationError",
    "InactiveUserError",
    "OrganizationNotActiveError",
    "PasswordHashingError",
    "SessionError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "SessionRevokedError",
]


class IdentityError(Exception):
    """Base error for users, organizations, and memberships."""


class IdentityValidationError(IdentityError):
    """A value offered as identity material is not well formed.

    Raised before storage is touched -- an empty display name, a login handle
    that is not an address, an identifier without its type prefix.
    """


class DuplicateIdentityError(IdentityError):
    """A record contradicts one already stored under the same unique key.

    Covers a repeated identifier, a repeated organization slug, a repeated
    normalized login handle, and a second membership for one (organization,
    user) pair. Never raised for a benign re-read: nothing in this layer
    upserts, so a duplicate write is always a genuine conflict.
    """


class IdentityNotFoundError(IdentityError):
    """A referenced user, organization, or membership is not stored."""


class InactiveUserError(IdentityError):
    """The user exists but is not ACTIVE.

    Raised where the distinction is an operator's concern -- creating a session
    for a disabled account, or resolving a session whose owner has since been
    disabled. It is deliberately *not* what a failed login raises: see
    ``AuthenticationFailedError``.
    """


class OrganizationNotActiveError(IdentityError):
    """The organization exists but its status forbids ordinary work in it.

    ``OrganizationStatus`` means "whether this organization's queues may be
    worked on", so a tenant that is not ACTIVE is one whose queues may not be.
    HTTP has enforced that since Sprint 13: ``review_api.tenancy`` refuses the
    whole scope and answers a generic 404, because telling a caller that a
    tenant exists but is suspended would publish a customer's commercial state
    to anyone who could guess an id.

    This error is the operator-side half of the same policy, added in Sprint 14
    Phase A. Creating a queue, granting a membership, or registering a workflow
    are ordinary business operations, and they are refused in a suspended
    organization rather than quietly succeeding -- otherwise "suspended" would
    mean only "unreachable over HTTP", and an operator command could keep
    growing a tenant that is supposed to be inert.

    Unlike the HTTP path this says plainly what is wrong. An operator is
    running a local administrative command against a database they already
    hold, so there is no existence to leak and an unexplained refusal would
    only invite a workaround.

    It is deliberately *not* raised by reads, by session resolution, or by
    ``apply_resolution``. Suspension makes a tenant inert; it does not delete
    its data, invalidate its history, or need a second enforcement point where
    HTTP already has one.
    """


class PasswordHashingError(IdentityError):
    """The password hasher could not produce a hash.

    An environment failure -- typically the Argon2 backend being unable to
    allocate the memory the parameters ask for -- and never a statement about
    the password. Kept distinct so a caller cannot mistake a broken host for a
    rejected credential.
    """


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


class AuthenticationError(IdentityError):
    """Base for failures on the login path."""


class AuthenticationFailedError(AuthenticationError):
    """The supplied credentials did not authenticate anyone.

    **One error for four different causes**, deliberately: an unknown login
    handle, a wrong password, a disabled account, and a user with no stored
    credential all raise exactly this, with the same message.

    Distinguishing them would publish an oracle. "No such account" tells a
    prober which addresses are registered; "account disabled" tells them a real
    person is behind an address and that the account is worth pursuing through
    another channel. Neither is information a caller who failed to authenticate
    has earned.

    The timing of these four paths is equalised separately -- see the dummy
    verification in ``identity.authentication`` -- because an identical message
    delivered in a tenth of the time is the same oracle in a different form.

    A storage failure is **not** this error. A database that cannot be read has
    not rejected anyone's credentials, and reporting it as a bad password would
    send an operator to look at the wrong thing.
    """


class CredentialNotFoundError(IdentityError):
    """No password credential is stored for this user.

    Internal only. On the login path this is converted into the generic
    ``AuthenticationFailedError`` before any caller sees it.
    """


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


class SessionError(IdentityError):
    """Base for session lifecycle failures."""


class SessionNotFoundError(SessionError):
    """No session matches this token.

    Also what a malformed or empty token produces. The two are not
    distinguished: telling a prober that their token was *well formed but
    unknown* confirms the format they guessed.
    """


class SessionExpiredError(SessionError):
    """The session is past one of its two bounds.

    ``reason`` names which -- ``"idle"`` or ``"absolute"``. The distinction is
    internal and useful (they mean different things operationally: one is an
    unattended browser, the other is a session that has simply lived long
    enough), and a later HTTP layer is free to collapse both into one answer.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class SessionRevokedError(SessionError):
    """The session was explicitly revoked and can never be used again."""
