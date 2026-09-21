"""Login, session, continue and logout over real HTTP.

The properties tested here are the ones the HTTP layer adds on top of the
already-proven Phase C core: that four different failures answer identically,
that the token leaves only in a cookie, that a session cannot be revived, and
that the bounds a browser is told about are the ones the server enforces.
"""

from __future__ import annotations

import pytest

from tests.review_api.auth_fixtures import (
    ALLOWED_ORIGIN,
    COOKIE_NAME,
    EMAIL,
    HOUR,
    MINUTE,
    PASSWORD,
    WRONG_PASSWORD,
    AuthFixture,
    disable,
)

LOGIN_URL = "/api/v1/auth/login"
SESSION_URL = "/api/v1/auth/session"
CONTINUE_URL = "/api/v1/auth/session/continue"
LOGOUT_URL = "/api/v1/auth/logout"


def unauthenticated(response) -> None:
    """The one public shape every authentication failure produces."""
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["details"] is None


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


def test_a_valid_login_returns_the_user_and_the_session(auth: AuthFixture) -> None:
    user = auth.create_user()

    response = auth.login()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"] == {"user_id": user.user_id, "display_name": user.display_name}
    assert body["session"]["idle_timeout_seconds"] == 3600
    assert body["session"]["warning_after_seconds"] == 3300
    assert body["session"]["absolute_timeout_seconds"] == 8 * HOUR


def test_login_publishes_the_policy_the_server_actually_enforces(auth: AuthFixture) -> None:
    """A browser timer built on these values warns at the right moment.

    The expiry timestamps and the policy come from the same session, so a
    frontend cannot drift from the server by computing its own thresholds.
    """
    auth.create_user()

    session = auth.login().json()["session"]

    from identity.clock import parse_utc_timestamp

    created = parse_utc_timestamp(session["created_at_utc"])
    assert parse_utc_timestamp(session["idle_expires_at_utc"]) - created == __import__(
        "datetime"
    ).timedelta(seconds=session["idle_timeout_seconds"])
    assert parse_utc_timestamp(session["absolute_expires_at_utc"]) - created == __import__(
        "datetime"
    ).timedelta(seconds=session["absolute_timeout_seconds"])


def test_the_login_body_never_carries_a_token_or_a_secret(auth: AuthFixture) -> None:
    """The raw token leaves in ``Set-Cookie`` and nowhere else."""
    auth.create_user()

    response = auth.login()
    token = response.cookies.get(COOKIE_NAME)
    raw_body = response.text

    assert token is not None
    assert token not in raw_body
    assert PASSWORD not in raw_body
    assert "argon2" not in raw_body
    for forbidden in ("token", "password", "credential", "hash"):
        assert forbidden not in raw_body.lower()


def test_the_login_body_carries_no_tenant_or_role(auth: AuthFixture) -> None:
    """A session establishes identity. Authorization is read later, fresh."""
    auth.create_user()

    raw_body = auth.login().text.lower()

    for forbidden in ("organization", "queue", "role", "membership"):
        assert forbidden not in raw_body


def test_a_user_with_no_membership_can_log_in(auth: AuthFixture) -> None:
    user = auth.create_user()
    assert auth.tenants.list_memberships_for_user(user.user_id) == ()

    assert auth.login().status_code == 200


@pytest.mark.parametrize(
    ("email", "password", "label"),
    [
        (EMAIL, WRONG_PASSWORD, "wrong password"),
        ("nobody@example.test", PASSWORD, "unknown address"),
        ("not-an-address", PASSWORD, "malformed handle"),
    ],
)
def test_every_bad_login_answers_identically(
    auth: AuthFixture,
    email: str,
    password: str,
    label: str,
) -> None:
    auth.create_user()

    response = auth.login(email=email, password=password)

    unauthenticated(response)
    assert COOKIE_NAME not in response.cookies, label


def test_a_disabled_account_answers_the_same_as_a_wrong_password(auth: AuthFixture) -> None:
    """ "This address exists but is switched off" is not something a caller earns."""
    user = auth.create_user()
    disable(auth, user)

    disabled = auth.login()
    wrong = auth.login(password=WRONG_PASSWORD)

    unauthenticated(disabled)
    assert disabled.json() == wrong.json()


def test_a_user_with_no_credential_answers_the_same(auth: AuthFixture) -> None:
    from identity.models import User

    auth.tenants.create_user(
        User.create(
            email="bare@example.test", display_name="Bare", created_at_utc="2026-09-12T07:00:00Z"
        )
    )

    unauthenticated(auth.login(email="bare@example.test"))


def test_a_failed_login_writes_no_session(auth: AuthFixture) -> None:
    """Nothing is written until the password is verified, so a failed attempt
    leaves no record that the handle was tried against a real account."""
    auth.create_user()

    auth.login(password=WRONG_PASSWORD)
    auth.login(email="nobody@example.test")

    assert auth.session_count() == 0


def test_a_storage_failure_is_not_collapsed_into_a_401(auth: AuthFixture) -> None:
    """A database that cannot be read has not rejected anyone's credentials.

    Reporting it as one would send an operator to investigate the user instead
    of the disk.
    """
    auth.create_user()
    auth.break_credential_storage()

    response = auth.tolerant_client().post(
        LOGIN_URL,
        json={"email": EMAIL, "password": PASSWORD},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    # And nothing about the failure reaches the caller.
    assert "password_credentials" not in response.text
    assert "sqlite" not in response.text.lower()


def test_an_extra_request_field_is_refused(auth: AuthFixture) -> None:
    """``extra="forbid"`` on the one request that establishes identity.

    A caller must not be able to smuggle a ``user_id`` or a ``role`` into it.
    """
    auth.create_user()

    response = auth.client.post(
        LOGIN_URL,
        json={"email": EMAIL, "password": PASSWORD, "organization": "acme"},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_validation_failure_never_echoes_the_password(auth: AuthFixture) -> None:
    response = auth.client.post(
        LOGIN_URL,
        json={"email": EMAIL, "password": PASSWORD, "extra": "x"},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert PASSWORD not in response.text


def test_login_is_reachable_only_as_a_post(auth: AuthFixture) -> None:
    assert auth.client.get(LOGIN_URL).status_code == 405


# --------------------------------------------------------------------------
# Reading the current session
# --------------------------------------------------------------------------


def test_a_valid_cookie_reads_the_session(signed_in: AuthFixture) -> None:
    response = signed_in.get_session()

    assert response.status_code == 200
    assert response.json()["user"]["display_name"] == "Reviewer One"


def test_reading_the_session_needs_no_origin(signed_in: AuthFixture) -> None:
    """A GET changes nothing, and the same-origin policy already stops a
    cross-site caller from reading the response."""
    assert signed_in.client.get(SESSION_URL).status_code == 200


def test_no_cookie_is_generic(auth: AuthFixture) -> None:
    unauthenticated(auth.get_session())


def test_a_random_cookie_answers_exactly_like_no_cookie(auth: AuthFixture) -> None:
    baseline = auth.get_session()
    auth.set_cookie("this-is-not-a-real-token")

    response = auth.get_session()

    unauthenticated(response)
    assert response.json() == baseline.json()


@pytest.mark.parametrize("token", ["", "   ", "x", "a.b.c", "x" * 200])
def test_every_malformed_cookie_answers_the_same(auth: AuthFixture, token: str) -> None:
    auth.set_cookie(token)

    unauthenticated(auth.get_session())


def test_a_revoked_session_is_generic(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    signed_in.sessions.revoke_session(token)

    unauthenticated(signed_in.get_session())


def test_an_idle_expired_session_is_generic(signed_in: AuthFixture) -> None:
    signed_in.clock.advance(60 * MINUTE)

    unauthenticated(signed_in.get_session())


def test_an_absolutely_expired_session_is_generic(signed_in: AuthFixture) -> None:
    signed_in.clock.advance(8 * HOUR)

    unauthenticated(signed_in.get_session())


def test_every_unusable_session_produces_one_identical_body(auth: AuthFixture) -> None:
    """Revoked, idle-expired, absolutely-expired and unknown are one answer.

    Telling them apart would tell whoever holds a token which of those it is --
    and "revoked" in particular says an operator noticed.
    """
    bodies = []

    auth.create_user()
    token = auth.login_as()
    auth.sessions.revoke_session(token)
    bodies.append(auth.get_session().json())

    auth.set_cookie(None)
    token = auth.login_as()
    auth.clock.advance(60 * MINUTE)
    bodies.append(auth.get_session().json())

    auth.set_cookie(None)
    token = auth.login_as()
    auth.clock.advance(8 * HOUR)
    bodies.append(auth.get_session().json())

    auth.set_cookie("unknown-token")
    bodies.append(auth.get_session().json())

    assert len({str(body) for body in bodies}) == 1, bodies


def test_no_expiry_reason_leaks_into_the_response(signed_in: AuthFixture) -> None:
    """``SessionExpiredError.reason`` is internal. It reaches the log, not here."""
    signed_in.clock.advance(9 * HOUR)

    text = signed_in.get_session().text.lower()

    for leak in ("idle", "absolute", "expired", "revoked", "disabled"):
        assert leak not in text


def test_disabling_the_owner_invalidates_a_live_session(signed_in: AuthFixture) -> None:
    """Checked on every request, so switching an account off takes effect at once."""
    user = signed_in.tenants.get_user_by_email(EMAIL)
    assert user is not None
    assert signed_in.get_session().status_code == 200

    disable(signed_in, user)

    unauthenticated(signed_in.get_session())


def test_a_disabled_owner_leaves_the_session_row_intact(signed_in: AuthFixture) -> None:
    """Expiry and refusal are validity decisions, never garbage collection."""
    token = signed_in.client.cookies.get(COOKIE_NAME)
    user = signed_in.tenants.get_user_by_email(EMAIL)
    assert user is not None
    disable(signed_in, user)

    signed_in.get_session()

    assert signed_in.stored_session(token) is not None


# --------------------------------------------------------------------------
# Continue
# --------------------------------------------------------------------------


def test_continue_returns_the_renewed_session(signed_in: AuthFixture) -> None:
    signed_in.clock.advance(30 * MINUTE)

    response = signed_in.continue_session()

    assert response.status_code == 200
    session = response.json()["session"]
    assert session["last_activity_at_utc"] > session["created_at_utc"]


def test_continue_creates_no_new_session_and_rotates_no_token(signed_in: AuthFixture) -> None:
    before = signed_in.client.cookies.get(COOKIE_NAME)
    signed_in.clock.advance(10 * MINUTE)

    response = signed_in.continue_session()

    assert signed_in.session_count() == 1
    assert COOKIE_NAME not in response.cookies
    assert signed_in.client.cookies.get(COOKIE_NAME) == before


def test_continue_cannot_revive_an_expired_session(signed_in: AuthFixture) -> None:
    signed_in.clock.advance(60 * MINUTE)

    unauthenticated(signed_in.continue_session())


def test_continue_on_a_revoked_session_is_generic(signed_in: AuthFixture) -> None:
    signed_in.sessions.revoke_session(signed_in.client.cookies.get(COOKIE_NAME))

    unauthenticated(signed_in.continue_session())


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------


def test_logout_revokes_the_session_and_clears_the_cookie(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)

    response = signed_in.logout()

    assert response.status_code == 200
    stored = signed_in.stored_session(token)
    assert stored is not None
    assert stored.is_revoked
    assert not signed_in.client.cookies.get(COOKIE_NAME)


def test_the_token_is_unusable_after_logout(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    signed_in.logout()

    signed_in.set_cookie(token)
    unauthenticated(signed_in.get_session())


def test_logout_is_idempotent(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    first = signed_in.logout()

    signed_in.set_cookie(token)
    second = signed_in.logout()

    assert first.status_code == second.status_code == 200


@pytest.mark.parametrize("token", [None, "unknown-token", ""])
def test_logout_succeeds_for_any_cookie_state(auth: AuthFixture, token: str | None) -> None:
    """Reporting "there was no session" would tell a caller whether the cookie
    they hold is live -- an oracle available without any credential."""
    auth.set_cookie(token)

    assert auth.logout().status_code == 200


def test_logout_succeeds_on_an_already_expired_session(signed_in: AuthFixture) -> None:
    signed_in.clock.advance(9 * HOUR)

    assert signed_in.logout().status_code == 200


def test_logout_does_not_renew_activity_before_revoking(signed_in: AuthFixture) -> None:
    """Touching a session on the way to ending it would be absurd.

    It also matters that logout does not use the principal dependency: that
    dependency raises on an expired session, which would turn "log me out" into
    a 401 for exactly the people who most want the cookie gone.
    """
    token = signed_in.client.cookies.get(COOKIE_NAME)
    before = signed_in.stored_session(token)
    assert before is not None
    signed_in.clock.advance(20 * MINUTE)

    signed_in.logout()

    after = signed_in.stored_session(token)
    assert after is not None
    assert after.last_activity_at_utc == before.last_activity_at_utc
    assert after.idle_expires_at_utc == before.idle_expires_at_utc


def test_one_users_logout_does_not_end_another_session(auth: AuthFixture) -> None:
    """Two browsers, two sessions. Revoking one must not end the other."""
    auth.create_user()
    first = auth.login_as()
    auth.set_cookie(None)
    second = auth.login_as()

    auth.set_cookie(first)
    auth.logout()

    auth.set_cookie(second)
    assert auth.get_session().status_code == 200
