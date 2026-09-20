"""The whole authentication chain, end to end, through production wiring.

Every other authentication test builds its services directly. That is the right
shape for testing what each one does, and the wrong shape for testing whether
an operator can actually get a working login -- because it skips the two seams
where a mistake is invisible: the operator command that creates the account,
and ``production_lifespan``, which decides what the served application is wired
with.

So this file does the real sequence, with nothing between the steps stubbed:

1. an operator creates an organization, a queue, and a user, through the real
   CLI, against a temporary database;
2. ``create_production_app()`` serves it through the real lifespan, which opens
   the database, resolves the queue, builds the Argon2 hasher and the session
   services, and loads the cookie and origin configuration from
   ``configs/auth_http.yaml``;
3. a browser-shaped client logs in, reads its session, continues it, logs out,
   and is refused afterwards.

The production hasher is used here -- real Argon2id at the library's own cost
-- because the point is that the default composition works, not that a cheap
one does. A handful of hashes is a price worth paying once.

``storage/review_queue.db`` is never created: both configuration seams are
redirected to ``tmp_path``, and the last test asserts it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import review_api.dependencies as api_dependencies
from review_api import create_production_app
from review_api.auth_config import AuthHttpConfig
from review_persistence.config import PROJECT_ROOT, ReviewPersistenceConfig
from scripts import manage_human_review

BASE = "/api/v1/auth"
COOKIE_NAME = "dq_session"
ALLOWED_ORIGIN = "http://127.0.0.1:5173"

ORGANIZATION_SLUG = "auth-smoke"
QUEUE_NAME = "production-review"
EMAIL = "reviewer@example.test"
DISPLAY_NAME = "Reviewer One"
# Long enough for the 12-character minimum, and not a credential for anything.
PASSWORD = "smoke test passphrase"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every production configuration seam at one temporary database.

    Both loaders are redirected, exactly as ``test_operational_smoke`` does:
    ``scripts.manage_human_review`` and ``review_api.dependencies`` each
    imported the persistence loader into their own namespace, so redirecting
    one and not the other is precisely the split-brain this rules out.

    The auth configuration is redirected too, to a loopback-only insecure
    policy -- the committed file's own shape -- because ``TestClient`` speaks
    http and would never return a ``Secure`` cookie.
    """
    database = tmp_path / "storage" / "review_queue.db"

    def _persistence() -> ReviewPersistenceConfig:
        return ReviewPersistenceConfig(
            database_path=database, busy_timeout_ms=2000, journal_mode="WAL"
        )

    def _auth() -> AuthHttpConfig:
        return AuthHttpConfig(
            session_cookie_name=COOKIE_NAME,
            session_cookie_secure=False,
            allowed_origins=(ALLOWED_ORIGIN,),
        )

    monkeypatch.setattr(manage_human_review, "load_review_persistence_config", _persistence)
    monkeypatch.setattr(api_dependencies, "load_review_persistence_config", _persistence)
    monkeypatch.setattr(api_dependencies, "load_auth_http_config", _auth)
    return database


def run_command(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", *argv])
    return manage_human_review.main()


def provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything an operator does before anyone can sign in.

    The queue exists because ``resolve_sole_review_queue`` refuses to start the
    server without exactly one -- the transitional binding Phase B documented,
    unchanged here.
    """
    assert (
        run_command(
            monkeypatch,
            "create-organization",
            "--slug",
            ORGANIZATION_SLUG,
            "--name",
            "Auth Smoke",
        )
        == 0
    )
    assert (
        run_command(
            monkeypatch,
            "create-review-queue",
            "--organization",
            ORGANIZATION_SLUG,
            "--name",
            QUEUE_NAME,
        )
        == 0
    )
    monkeypatch.setattr(manage_human_review.getpass, "getpass", lambda prompt="": PASSWORD)
    assert (
        run_command(
            monkeypatch,
            "create-user",
            "--email",
            EMAIL,
            "--display-name",
            DISPLAY_NAME,
        )
        == 0
    )


def serve() -> TestClient:
    """The real production application, with the lifespan that owns everything."""
    return TestClient(create_production_app())


def test_the_whole_authentication_chain_works_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    configured: Path,
) -> None:
    """Provision, sign in, work, continue, sign out, and be refused."""
    provision(monkeypatch)
    assert configured.exists()

    with serve() as client:
        # Unauthenticated to begin with.
        assert client.get(f"{BASE}/session").status_code == 401

        login = client.post(
            f"{BASE}/login",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["user"]["display_name"] == DISPLAY_NAME
        assert body["session"]["idle_timeout_seconds"] == 3600
        assert body["session"]["absolute_timeout_seconds"] == 28800
        # The token left in the cookie and nowhere else.
        token = client.cookies.get(COOKIE_NAME)
        assert token is not None
        assert token not in login.text
        assert PASSWORD not in login.text

        # The cookie the client captured is enough to read the session.
        session = client.get(f"{BASE}/session")
        assert session.status_code == 200
        assert session.json()["user"]["user_id"] == body["user"]["user_id"]

        # Continuing renews the idle window and returns the same shape.
        continued = client.post(f"{BASE}/session/continue", headers={"Origin": ALLOWED_ORIGIN})
        assert continued.status_code == 200
        assert (
            continued.json()["session"]["absolute_expires_at_utc"]
            == body["session"]["absolute_expires_at_utc"]
        )

        # A forged origin is refused even with a perfectly good cookie.
        assert (
            client.post(
                f"{BASE}/session/continue", headers={"Origin": "http://evil.test"}
            ).status_code
            == 403
        )

        logout = client.post(f"{BASE}/logout", headers={"Origin": ALLOWED_ORIGIN})
        assert logout.status_code == 200
        assert not client.cookies.get(COOKIE_NAME)

        # And the token is dead, even if a client kept a copy of it.
        client.cookies.set(COOKIE_NAME, token)
        refused = client.get(f"{BASE}/session")
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "UNAUTHENTICATED"


def test_the_served_application_loads_the_committed_cookie_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The real ``configs/auth_http.yaml`` is what production would serve with.

    Loaded here rather than substituted, so a change to the committed file that
    made it unloadable -- or that widened it past loopback while the cookie is
    insecure -- fails in this suite rather than at an operator's first login.
    """
    from review_api.auth_config import load_auth_http_config

    config = load_auth_http_config()

    assert config.session_cookie_name == "dq_session"
    assert config.allowed_origins
    assert all(origin.startswith("http://127.0.0.1") for origin in config.allowed_origins)


def test_review_routes_are_still_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
    configured: Path,
) -> None:
    """The phase boundary, asserted rather than assumed.

    Adding identity to the review routes without tenant authorization would
    produce a surface where any signed-in user reads every tenant's queue --
    worse than an honestly unauthenticated one. So they stay exactly as they
    were until the phase that scopes them.
    """
    provision(monkeypatch)

    with serve() as client:
        response = client.get("/api/v1/review-cases")

        assert response.status_code == 200
        assert response.json()["total"] == 0


def test_health_is_still_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
    configured: Path,
) -> None:
    """A liveness probe that needed a credential would need one to be issued."""
    provision(monkeypatch)

    with serve() as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_the_production_database_is_never_created() -> None:
    """Guards the fixture wiring for this module."""
    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
