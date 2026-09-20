"""Origin validation, cookie attributes, and the activity policy.

Three separate security properties, tested together because they are the three
things the HTTP layer adds that no amount of Phase C correctness provides:

* **Origin** is the CSRF control we enforce ourselves, alongside the
  ``SameSite=Strict`` the browser enforces for us.
* **Cookie attributes** decide whether the bearer token is readable by script,
  sent over plain HTTP, or left on disk after the browser closes.
* **The activity policy** decides which requests keep a session alive, and --
  more importantly -- which do not.
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from pathlib import Path

import pytest

from identity.clock import parse_utc_timestamp
from review_api.auth_config import (
    AuthHttpConfig,
    AuthHttpConfigurationError,
    load_auth_http_config,
    normalize_origin,
)
from review_persistence.config import PROJECT_ROOT
from tests.review_api.auth_fixtures import (
    ALLOWED_ORIGIN,
    COOKIE_NAME,
    HOUR,
    MINUTE,
    PASSWORD,
    AuthFixture,
    build_auth_fixture,
)

LOGIN_URL = "/api/v1/auth/login"
CONTINUE_URL = "/api/v1/auth/session/continue"
LOGOUT_URL = "/api/v1/auth/logout"


def set_cookie_header(response) -> str:
    header = response.headers.get("set-cookie")
    assert header is not None, "no Set-Cookie header"
    return header


def cookie_attributes(response) -> SimpleCookie:
    parsed = SimpleCookie()
    parsed.load(set_cookie_header(response))
    return parsed


# --------------------------------------------------------------------------
# Origin: the configured allow-list
# --------------------------------------------------------------------------


def test_the_exact_configured_origin_is_accepted(auth: AuthFixture) -> None:
    auth.create_user()

    assert auth.login(origin=ALLOWED_ORIGIN).status_code == 200


@pytest.mark.parametrize(
    ("origin", "attack"),
    [
        ("http://127.0.0.1:5173.evil.test", "suffix: the allowed origin as a prefix of a host"),
        ("http://evil-127.0.0.1:5173", "prefix: the allowed host embedded in a longer one"),
        ("http://evil.test/http://127.0.0.1:5173", "the allowed origin hidden in a path"),
        ("https://127.0.0.1:5173", "same host and port, different scheme"),
        ("http://127.0.0.1:5174", "same host and scheme, different port"),
        ("http://127.0.0.2:5173", "a different loopback address"),
        ("http://localhost:5173", "a name that resolves to the allowed address"),
        ("http://evil.test", "an unrelated origin"),
        ("null", "the opaque origin a sandboxed frame sends"),
        ("", "an empty header"),
    ],
)
def test_every_near_miss_origin_is_refused(
    auth: AuthFixture,
    origin: str,
    attack: str,
) -> None:
    """Whole-origin comparison, so none of these is a near enough miss.

    Each entry is a way a careless check fails: ``startswith`` lets the second
    through, ``endswith`` the first, substring matching the third, and ignoring
    scheme or port the fourth and fifth. ``localhost`` is refused even though
    it resolves to the allowed address, because resolution is not ours to
    trust and the browser sends the name it was given.
    """
    auth.create_user()

    response = auth.login(origin=origin)

    assert response.status_code == 403, attack
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_missing_origin_is_refused(auth: AuthFixture) -> None:
    """Fail closed.

    Every browser sends ``Origin`` on a cross-origin request and on any
    same-origin POST, so its absence means the caller is not the browser this
    API is built for. Treating absence as trustworthy is the standard way this
    check is bypassed -- a forger who can suppress the header would face no
    check at all.
    """
    auth.create_user()

    response = auth.login(origin=None)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_refused_origin_creates_no_session(auth: AuthFixture) -> None:
    """The check runs before any authentication work.

    Correct credentials with a forged origin must cost nothing: no password
    verification, no session row, no cookie.
    """
    auth.create_user()

    response = auth.login(origin="http://evil.test")

    assert response.status_code == 403
    assert auth.session_count() == 0
    assert "set-cookie" not in response.headers


def test_a_refused_origin_does_not_reveal_the_allow_list(auth: AuthFixture) -> None:
    """Naming the trusted origin would tell a forger exactly what to obtain.

    The attacker-controlled value is not echoed either.
    """
    auth.create_user()

    body = auth.login(origin="http://evil.test").text

    assert ALLOWED_ORIGIN not in body
    assert "evil.test" not in body
    assert "127.0.0.1" not in body


def test_continue_and_logout_are_origin_protected(signed_in: AuthFixture) -> None:
    for call in (signed_in.continue_session, signed_in.logout):
        assert call(origin="http://evil.test").status_code == 403
        assert call(origin=None).status_code == 403


def test_a_refused_origin_does_not_revoke_the_session(signed_in: AuthFixture) -> None:
    """A forced logout is a real, if minor, cross-site attack."""
    token = signed_in.client.cookies.get(COOKIE_NAME)

    signed_in.logout(origin="http://evil.test")

    stored = signed_in.stored_session(token)
    assert stored is not None
    assert not stored.is_revoked
    assert signed_in.get_session().status_code == 200


def test_reading_the_session_is_not_origin_protected(signed_in: AuthFixture) -> None:
    """A GET changes nothing, so refusing it would buy nothing and break links."""
    assert signed_in.client.get("/api/v1/auth/session").status_code == 200


def test_no_cors_header_is_ever_sent(signed_in: AuthFixture) -> None:
    """CORS permits cross-origin requests; this API has none to permit.

    The browser reaches it same-origin through the Vite proxy. Adding CORS to
    make something pass would hand browser-mediated access to customer data.
    """
    for response in (
        signed_in.get_session(),
        signed_in.continue_session(),
        signed_in.client.options("/api/v1/auth/session"),
    ):
        for header in response.headers:
            assert not header.lower().startswith("access-control-")


# --------------------------------------------------------------------------
# Origin parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "normalized"),
    [
        ("http://127.0.0.1:5173", "http://127.0.0.1:5173"),
        ("HTTP://127.0.0.1:5173", "http://127.0.0.1:5173"),
        ("https://Example.Test", "https://example.test"),
        ("http://example.test:80", "http://example.test"),
        ("https://example.test:443", "https://example.test"),
        ("  http://example.test  ", "http://example.test"),
    ],
)
def test_origins_normalize_to_the_form_a_browser_sends(written: str, normalized: str) -> None:
    """Both sides of the comparison go through this, so they cannot disagree."""
    assert normalize_origin(written) == normalized


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "example.test",
        "ftp://example.test",
        "http://example.test/path",
        "http://example.test?q=1",
        "http://example.test#frag",
        "http://user:pass@example.test",
        "http://",
        "null",
    ],
)
def test_a_malformed_origin_is_refused_rather_than_trimmed(value: str) -> None:
    """Silently discarding the extra part is how a path becomes a match."""
    with pytest.raises(AuthHttpConfigurationError):
        normalize_origin(value)


def test_there_is_no_wildcard(auth: AuthFixture) -> None:
    config = AuthHttpConfig(session_cookie_secure=True, allowed_origins=("https://example.test",))

    for candidate in ("*", "https://*.example.test", "https://anything.example.test"):
        assert config.is_allowed_origin(candidate) is False


def test_an_unconfigured_application_trusts_no_origin() -> None:
    """The default allow-list is empty, so an unconfigured app refuses everything."""
    config = AuthHttpConfig()

    assert config.allowed_origins == ()
    assert config.is_allowed_origin("https://example.test") is False


# --------------------------------------------------------------------------
# Configuration safety
# --------------------------------------------------------------------------


def test_the_cookie_is_secure_by_default() -> None:
    """The safe value is what you get for not deciding."""
    assert AuthHttpConfig().session_cookie_secure is True


def test_insecure_cookies_are_confined_to_loopback_origins() -> None:
    """Writing ``secure: false`` is not enough on its own.

    The setting cannot be combined with a real deployment's origins at all: a
    config that tried fails to load rather than serving a bearer token over
    plain HTTP to something reachable from the network.
    """
    AuthHttpConfig(session_cookie_secure=False, allowed_origins=("http://127.0.0.1:5173",))
    AuthHttpConfig(session_cookie_secure=False, allowed_origins=("http://localhost:5173",))

    with pytest.raises(AuthHttpConfigurationError, match="loopback"):
        AuthHttpConfig(session_cookie_secure=False, allowed_origins=("https://example.test",))
    with pytest.raises(AuthHttpConfigurationError, match="loopback"):
        AuthHttpConfig(session_cookie_secure=False, allowed_origins=("http://example.test",))


def test_the_committed_local_config_loads_and_is_loopback_only() -> None:
    """The shipped file is the documented local runtime, and nothing wider."""
    config = load_auth_http_config(PROJECT_ROOT / "configs" / "auth_http.yaml")

    assert config.session_cookie_name == "dq_session"
    assert config.session_cookie_secure is False
    assert all(origin.startswith("http://127.0.0.1:") for origin in config.allowed_origins)


def test_an_unrecognised_secure_value_is_not_treated_as_false(tmp_path: Path) -> None:
    """A typo must not silently disable the cookie's Secure attribute."""
    path = tmp_path / "auth.yaml"
    path.write_text("session_cookie_secure: 'no'\n", encoding="utf-8")

    with pytest.raises(AuthHttpConfigurationError, match="boolean"):
        load_auth_http_config(path)


@pytest.mark.parametrize("name", ["", "   ", "bad name", "bad;name", "bad=name"])
def test_a_cookie_name_that_is_not_a_token_is_refused(name: str) -> None:
    with pytest.raises(AuthHttpConfigurationError):
        AuthHttpConfig(session_cookie_name=name)


# --------------------------------------------------------------------------
# Cookie attributes
# --------------------------------------------------------------------------


def test_the_session_cookie_has_every_attribute_it_needs(auth: AuthFixture) -> None:
    auth.create_user()

    response = auth.login()
    header = set_cookie_header(response).lower()
    morsel = cookie_attributes(response)[COOKIE_NAME]

    assert "httponly" in header, "script must not be able to read the token"
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/"
    assert not morsel["domain"], "a Domain would widen the cookie to every subdomain"


def test_the_cookie_is_not_persistent(auth: AuthFixture) -> None:
    """A browser-session cookie, gone when the browser closes.

    The server-side session may live up to eight hours, but persisting the
    cookie across restarts would leave a usable credential on disk after the
    person walked away -- and that is what "remember me" means.
    """
    auth.create_user()

    morsel = cookie_attributes(auth.login())[COOKIE_NAME]

    assert not morsel["max-age"]
    assert not morsel["expires"]


def test_secure_appears_when_it_is_configured(tmp_path: Path) -> None:
    """The default is true; this proves the attribute is actually emitted.

    The other tests run with ``secure=False`` only because ``TestClient``
    speaks http and would otherwise never return the cookie.
    """
    for fixture in build_auth_fixture(tmp_path, cookie_secure=True):
        fixture.create_user()
        header = set_cookie_header(fixture.login()).lower()

        assert "secure" in header


def test_secure_is_absent_only_in_the_explicit_insecure_mode(auth: AuthFixture) -> None:
    auth.create_user()

    assert auth.config.session_cookie_secure is False
    assert "secure" not in set_cookie_header(auth.login()).lower()


def test_the_cookie_value_is_the_opaque_token_and_nothing_else(auth: AuthFixture) -> None:
    """No user id, no session id, no JWT. There is nothing in it to read."""
    user = auth.create_user()

    response = auth.login()
    token = response.cookies.get(COOKIE_NAME)
    stored = auth.stored_session(token)

    assert stored is not None
    assert token != user.user_id
    assert token != stored.session_id
    assert user.user_id not in token
    assert stored.session_id not in token
    # A JWT is three base64 segments separated by dots.
    assert token.count(".") == 0
    assert not token.startswith("ey")


def test_the_database_holds_the_digest_and_never_the_cookie(auth: AuthFixture) -> None:
    auth.create_user()
    token = auth.login_as()

    connection = auth.database.connect()
    tables = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 - schema names
            for value in tuple(row):
                assert value != token
                assert not (isinstance(value, str) and PASSWORD in value)

    from identity.sessions import hash_session_token

    stored = auth.stored_session(token)
    assert stored is not None
    assert stored.token_hash == hash_session_token(token)


def test_logout_clears_the_cookie_with_the_same_scope_that_set_it(signed_in: AuthFixture) -> None:
    """A browser matches a deletion by name, path and domain.

    Clearing with a different path would leave the original cookie in place
    while appearing to succeed.
    """
    login_morsel = cookie_attributes(signed_in.login())[COOKIE_NAME]

    response = signed_in.logout()
    clear_morsel = cookie_attributes(response)[COOKIE_NAME]

    assert clear_morsel["path"] == login_morsel["path"] == "/"
    assert clear_morsel["samesite"].lower() == "strict"
    assert not clear_morsel["domain"]
    # A deletion is an empty value with an expiry in the past.
    assert clear_morsel.value == ""
    assert clear_morsel["max-age"] or clear_morsel["expires"]


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


def idle_expiry(fixture: AuthFixture, token: str):
    stored = fixture.stored_session(token)
    assert stored is not None
    return parse_utc_timestamp(stored.idle_expires_at_utc)


def test_login_records_activity_at_the_moment_of_login(auth: AuthFixture) -> None:
    auth.create_user()

    token = auth.login_as()

    stored = auth.stored_session(token)
    assert stored is not None
    assert stored.last_activity_at_utc == stored.created_at_utc


def test_an_authenticated_read_renews_the_idle_window(signed_in: AuthFixture) -> None:
    """A reviewer working steadily is not logged out for failing to press a button."""
    token = signed_in.client.cookies.get(COOKIE_NAME)
    before = idle_expiry(signed_in, token)
    signed_in.clock.advance(30 * MINUTE)

    signed_in.get_session()

    after = idle_expiry(signed_in, token)
    assert after > before
    assert (after - before).total_seconds() == 30 * MINUTE


def test_continue_renews_the_idle_window_exactly_once(signed_in: AuthFixture) -> None:
    """One request, one activity record.

    Two renewals would be harmless in effect but would make that statement
    false, and the difference is what this pins.
    """
    token = signed_in.client.cookies.get(COOKIE_NAME)
    signed_in.clock.advance(20 * MINUTE)

    response = signed_in.continue_session()

    reported = response.json()["session"]
    stored = signed_in.stored_session(token)
    assert stored is not None
    # The body reports the post-renewal state, and it matches storage exactly:
    # a second touch would have moved storage past what was reported.
    assert reported["last_activity_at_utc"] == stored.last_activity_at_utc
    assert reported["idle_expires_at_utc"] == stored.idle_expires_at_utc


def test_the_session_response_reports_post_renewal_timestamps(signed_in: AuthFixture) -> None:
    """Pre-touch values would tell a client it expires earlier than it does."""
    signed_in.clock.advance(15 * MINUTE)

    session = signed_in.get_session().json()["session"]

    assert session["last_activity_at_utc"] > session["created_at_utc"]
    idle = parse_utc_timestamp(session["idle_expires_at_utc"])
    activity = parse_utc_timestamp(session["last_activity_at_utc"])
    assert (idle - activity).total_seconds() == 60 * MINUTE


def test_an_invalid_cookie_renews_nothing(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    before = idle_expiry(signed_in, token)
    signed_in.clock.advance(10 * MINUTE)

    signed_in.set_cookie("not-a-real-token")
    signed_in.get_session()

    assert idle_expiry(signed_in, token) == before


def test_an_expired_session_is_not_revived_by_being_used(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    signed_in.clock.advance(60 * MINUTE)

    for _ in range(3):
        assert signed_in.get_session().status_code == 401
        assert signed_in.continue_session().status_code == 401

    stored = signed_in.stored_session(token)
    assert stored is not None
    assert stored.idle_expires_at_utc < stored.absolute_expires_at_utc


def test_a_refused_origin_renews_nothing(signed_in: AuthFixture) -> None:
    token = signed_in.client.cookies.get(COOKIE_NAME)
    before = idle_expiry(signed_in, token)
    signed_in.clock.advance(10 * MINUTE)

    signed_in.continue_session(origin="http://evil.test")
    signed_in.continue_session(origin=None)

    assert idle_expiry(signed_in, token) == before


def test_no_amount_of_activity_extends_the_absolute_bound(signed_in: AuthFixture) -> None:
    """The bound that limits a stolen token.

    The session is used every half hour for the whole eight hours, so idle
    expiry is never reached -- and it ends anyway, exactly on time.
    """
    token = signed_in.client.cookies.get(COOKIE_NAME)
    original = signed_in.stored_session(token)
    assert original is not None
    absolute = original.absolute_expires_at_utc

    for _ in range(15):  # 7h30m
        signed_in.clock.advance(30 * MINUTE)
        assert signed_in.continue_session().status_code == 200
        stored = signed_in.stored_session(token)
        assert stored is not None
        assert stored.absolute_expires_at_utc == absolute

    signed_in.clock.advance(29 * MINUTE + 59)
    assert signed_in.get_session().status_code == 200

    signed_in.clock.advance(1)  # exactly 8h
    assert signed_in.get_session().status_code == 401
    assert signed_in.continue_session().status_code == 401


def test_the_warning_threshold_is_not_an_expiry(signed_in: AuthFixture) -> None:
    """55 minutes is when a frontend should warn, not when the server stops."""
    signed_in.clock.advance(55 * MINUTE)

    response = signed_in.get_session()

    assert response.status_code == 200
    assert response.json()["session"]["warning_after_seconds"] == 55 * MINUTE


def test_a_login_after_an_expiry_starts_a_fresh_absolute_window(auth: AuthFixture) -> None:
    """Signing in again is a new session, not a revived one."""
    auth.create_user()
    first = auth.login_as()
    auth.clock.advance(9 * HOUR)
    assert auth.get_session().status_code == 401

    auth.set_cookie(None)
    second = auth.login_as()

    assert second != first
    assert auth.get_session().status_code == 200
    assert auth.session_count() == 2
