"""Login: authenticate, then start a session. Nothing else.

A deliberately thin composition of two services that do not know about each
other. ``AuthenticationService`` verifies a password and has never heard of
sessions; ``SessionService`` mints a session for a user id and has never heard
of passwords. Neither had to grow a dependency for login to exist, and either
can be used alone -- a future operator command that verifies a password without
starting a session, or a session created by some other means of authentication.

There is no HTTP here: no cookie, no request, no response. This returns the raw
token to its caller exactly once, and a later phase decides how that token
reaches a browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from identity.authentication import AuthenticatedUser, AuthenticationService
from identity.session_service import SessionService
from identity.sessions import CreatedSession

__all__ = ["LoginResult", "LoginService"]


@dataclass(frozen=True)
class LoginResult:
    """Who logged in, and the session that was started for them.

    The raw token is reachable as ``created.raw_token`` and is excluded from
    every ``repr`` on the way -- see ``CreatedSession``. This object is
    therefore safe to log; the attribute inside it is not, and is never
    rendered by accident.
    """

    user: AuthenticatedUser
    created: CreatedSession


class LoginService:
    """One operation: credentials in, authenticated session out."""

    def __init__(
        self,
        *,
        authentication: AuthenticationService,
        sessions: SessionService,
    ) -> None:
        self._authentication = authentication
        self._sessions = sessions

    def login(self, login_handle: str, password: str) -> LoginResult:
        """Verify credentials and start a session, or raise.

        Order matters and is not interchangeable: nothing is written until the
        password has been verified, so a failed login leaves no session row, no
        token, and no trace that the handle was even tried against a real
        account.

        A failure raises ``AuthenticationFailedError`` -- the same generic
        error for an unknown handle, a wrong password, a disabled account and a
        missing credential. Membership is never consulted; a user who belongs
        to no organization logs in successfully and finds nothing to read,
        which is an authorization answer given later by code that can see the
        membership table.
        """
        user = self._authentication.authenticate(login_handle, password)
        created = self._sessions.create_session(user.user_id)
        return LoginResult(user=user, created=created)
