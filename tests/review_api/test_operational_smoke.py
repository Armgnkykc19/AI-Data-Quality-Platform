"""The whole of the secured system in one chain: provision, sign in, decide.

Every other test in this package substitutes something. This file substitutes
nothing that matters: the tenants, queues, users and memberships are created by
running the real ``manage_human_review`` commands, the workflows are registered
by the real pipeline over real CSVs, the application is the one
``create_production_app`` builds through the real ``production_lifespan``, and
the browser-shaped client really logs in with a password and really carries a
cookie.

What it exists to prove is the set of properties that only appear when all of
that is assembled at once:

* the production application serves **more than one review queue**, which it
  refused to do one phase ago;
* the queue a request reaches is the one its URL names, and nothing else;
* a member of one organization cannot reach another's queue, and cannot learn
  that it exists;
* a VIEWER reads and cannot decide; a REVIEWER decides;
* the reviewer id in the durable event is the authenticated user's, not
  anything a client sent;
* the same ``review_case_id`` can exist in two queues without either one
  leaking into the other;
* ``POST .../resolve`` is refused without a trusted ``Origin``;
* ``/health`` is still public and the old unscoped review paths are gone.

The database-path integration is still the thing most worth pinning. The
bootstrap command and the API runtime each read ``configs/review_persistence.yaml``
through ``load_review_persistence_config`` -- from different modules -- and an
operator who bootstrapped one database and served another would see a queue
that was permanently, silently empty. Both seams are redirected to one
``tmp_path`` config, and the commands run *without* ``--review-db`` so the
default resolution is what gets exercised.

No network, no provider, no golden or holdout data, and
``storage/review_queue.db`` is never created. No password, raw token, password
hash or token digest is ever printed or asserted on.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import review_api.dependencies as api_dependencies
from human_review.models import ReviewStatus
from review_api import create_production_app
from review_api.auth_config import AuthHttpConfig
from review_persistence.config import PROJECT_ROOT, ReviewPersistenceConfig
from review_persistence.sqlite import SqliteTenantRepository, open_review_database
from scripts import manage_human_review

COOKIE_NAME = "dq_session"
ALLOWED_ORIGIN = "http://127.0.0.1:5173"
FOREIGN_ORIGIN = "http://evil.test"

# Two organizations. Organization A owns two queues, which is the arrangement
# the previous phase's startup binding could not serve at all.
ORGANIZATION_A = "operational-smoke-a"
ORGANIZATION_B = "operational-smoke-b"
QUEUE_A1 = "queue-one"
QUEUE_A2 = "queue-two"
QUEUE_B1 = "queue-b"

REVIEWER_EMAIL = "reviewer@example.test"
VIEWER_EMAIL = "viewer@example.test"
OUTSIDER_EMAIL = "outsider@example.test"
# Long enough for the policy minimum, and a credential for nothing.
PASSWORD = "smoke test passphrase"

# Two rows sharing an exact email while conflicting on company, city, district
# and address: strong identity evidence, a score below AUTO_MATCH, and therefore
# exactly one REVIEW case from the real entity-resolution engine.
SHARED_CSV = (
    "first_name,last_name,email,phone,company,city,district,address\n"
    "Ali,Yilmaz,ali@example.com,,Acme,Ankara,Cankaya,Street 1\n"
    "Ali,Yilmaz,ali@example.com,,Beta,Izmir,Konak,Other 9\n"
)

# A different record pair for queue A2. It deliberately still produces the same
# deterministic review_case_id -- the id comes from the positional record
# identifiers, which any two-row file shares -- so A1 and A2 differ only in the
# customer data behind that id. That is the hardest arrangement for queue
# scoping to get right, and the one the isolation tests below rely on.
OTHER_CSV = (
    "first_name,last_name,email,phone,company,city,district,address\n"
    "Veli,Demir,veli@example.com,,Gamma,Bursa,Nilufer,Road 3\n"
    "Veli,Demir,veli@example.com,,Delta,Adana,Seyhan,Road 77\n"
)


@dataclass(frozen=True)
class Tenants:
    """The opaque ids the URLs are built from, read back from storage.

    Read rather than scraped from command output: the ids are what the tenant
    graph actually holds, and parsing them out of a terminal line would couple
    this test to a print statement.
    """

    organization_a: str
    organization_b: str
    queue_a1: str
    queue_a2: str
    queue_b1: str


def cases_url(organization_id: str, review_queue_id: str) -> str:
    return f"/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every production configuration seam at one temporary database.

    Both persistence loaders are redirected, because ``scripts.manage_human_review``
    and ``review_api.dependencies`` each imported it into their own namespace --
    redirecting one and not the other is precisely the split-brain this rules
    out.

    The auth configuration is redirected too, to a loopback-only insecure
    policy of exactly the committed file's shape, because ``TestClient`` speaks
    http and would never return a ``Secure`` cookie. The origin list is real and
    the check is the real one.
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
    """Invoke the operator CLI the way an operator types it."""
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", *argv])
    return manage_human_review.main()


def create_user(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    """The password is prompted for, never passed as an argument."""
    monkeypatch.setattr(manage_human_review.getpass, "getpass", lambda prompt="": PASSWORD)
    assert run_command(monkeypatch, "create-user", "--email", email, "--display-name", email) == 0


def register(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    csv: str,
    name: str,
    organization: str,
    queue: str,
) -> None:
    """Run the real generate-and-register chain into one existing queue."""
    csv_path = tmp_path / f"{name}.csv"
    csv_path.write_text(csv, encoding="utf-8")
    assert (
        run_command(
            monkeypatch,
            "generate",
            str(csv_path),
            "--report-dir",
            str(tmp_path / f"report-{name}"),
            "--register-review-queue",
            "--organization",
            organization,
            "--review-queue",
            queue,
        )
        == 0
    )


def provision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database: Path) -> Tenants:
    """Everything an operator does before anyone can sign in and review.

    Ten explicit steps, and not one of them happens as a side effect of
    another: the queue does not create the organization, registration does not
    create the queue, and no command grants a membership on its own.
    """
    for slug in (ORGANIZATION_A, ORGANIZATION_B):
        assert run_command(monkeypatch, "create-organization", "--slug", slug, "--name", slug) == 0
    for organization, queue in (
        (ORGANIZATION_A, QUEUE_A1),
        (ORGANIZATION_A, QUEUE_A2),
        (ORGANIZATION_B, QUEUE_B1),
    ):
        assert (
            run_command(
                monkeypatch, "create-review-queue", "--organization", organization, "--name", queue
            )
            == 0
        )

    for email in (REVIEWER_EMAIL, VIEWER_EMAIL, OUTSIDER_EMAIL):
        create_user(monkeypatch, email)

    # Explicit user, explicit organization, explicit role. The outsider gets
    # nothing, which is what makes them an outsider.
    assert (
        run_command(
            monkeypatch,
            "add-membership",
            "--user",
            REVIEWER_EMAIL,
            "--organization",
            ORGANIZATION_A,
            "--role",
            "REVIEWER",
        )
        == 0
    )
    assert (
        run_command(
            monkeypatch,
            "add-membership",
            "--user",
            VIEWER_EMAIL,
            "--organization",
            ORGANIZATION_A,
            "--role",
            "VIEWER",
        )
        == 0
    )

    # The same CSV into A1 and B1, so both queues hold a case with the same
    # deterministic review_case_id. That collision is ordinary -- the id is
    # derived from the reviewed record pair -- and it is exactly what tenant
    # scoping has to keep apart.
    register(
        monkeypatch,
        tmp_path,
        csv=SHARED_CSV,
        name="a1",
        organization=ORGANIZATION_A,
        queue=QUEUE_A1,
    )
    register(
        monkeypatch,
        tmp_path,
        csv=OTHER_CSV,
        name="a2",
        organization=ORGANIZATION_A,
        queue=QUEUE_A2,
    )
    register(
        monkeypatch,
        tmp_path,
        csv=SHARED_CSV,
        name="b1",
        organization=ORGANIZATION_B,
        queue=QUEUE_B1,
    )
    return read_tenants(database)


def read_tenants(database: Path) -> Tenants:
    connection = open_review_database(
        ReviewPersistenceConfig(database_path=database, busy_timeout_ms=2000, journal_mode="WAL")
    )
    try:
        tenants = SqliteTenantRepository(connection)
        organization_a = tenants.get_organization_by_slug(ORGANIZATION_A)
        organization_b = tenants.get_organization_by_slug(ORGANIZATION_B)
        assert organization_a is not None and organization_b is not None

        def queue_id(organization_id: str, name: str) -> str:
            queue = tenants.get_review_queue_by_name(organization_id=organization_id, name=name)
            assert queue is not None
            return queue.review_queue_id

        return Tenants(
            organization_a=organization_a.organization_id,
            organization_b=organization_b.organization_id,
            queue_a1=queue_id(organization_a.organization_id, QUEUE_A1),
            queue_a2=queue_id(organization_a.organization_id, QUEUE_A2),
            queue_b1=queue_id(organization_b.organization_id, QUEUE_B1),
        )
    finally:
        connection.close()


@pytest.fixture
def tenants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, configured: Path) -> Tenants:
    return provision(monkeypatch, tmp_path, configured)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The real production application, with the lifespan that owns everything."""
    with TestClient(create_production_app()) as test_client:
        yield test_client


def sign_in(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200, response.status_code
    # The token is in the cookie jar and nowhere this test prints.
    assert client.cookies.get(COOKIE_NAME) is not None


def only_case_id(client: TestClient, organization_id: str, review_queue_id: str) -> str:
    page = client.get(cases_url(organization_id, review_queue_id)).json()
    assert page["total"] == 1, page["total"]
    return page["items"][0]["review_case_id"]


# --------------------------------------------------------------------------
# The operator chain, and a multi-queue production application
# --------------------------------------------------------------------------


def test_the_production_application_serves_more_than_one_queue(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """Three queues across two organizations, and the server starts anyway.

    The previous phase refused to start against exactly this database: with no
    authenticated caller there was nothing that could choose between queues, so
    it resolved one at startup and treated two as ambiguous. A request now
    names its own queue, so ambiguity is not a thing the process can have.
    """
    sign_in(client, REVIEWER_EMAIL)

    assert client.get(cases_url(tenants.organization_a, tenants.queue_a1)).status_code == 200
    assert client.get(cases_url(tenants.organization_a, tenants.queue_a2)).status_code == 200


def test_each_url_selects_its_own_queue(tenants: Tenants, client: TestClient) -> None:
    """Two queues in one organization answer about two different record pairs.

    The two queues were registered from different CSVs and still hold the same
    ``review_case_id`` -- ``stable_review_case_id`` derives it from the reviewed
    pair's positional record identifiers, which two two-row files share. That
    collision is exactly the condition queue scoping exists for, so the
    distinguishing assertion is on the evidence rather than the id: the blocking
    key is customer-derived and differs between the two files.
    """
    sign_in(client, REVIEWER_EMAIL)

    def blocking_key(review_queue_id: str) -> str:
        base = cases_url(tenants.organization_a, review_queue_id)
        case_id = only_case_id(client, tenants.organization_a, review_queue_id)
        reasons = client.get(f"{base}/{case_id}").json()["blocking_reasons"]
        assert reasons
        return reasons[0]["blocking_key"]

    assert blocking_key(tenants.queue_a1) != blocking_key(tenants.queue_a2)


def test_one_queues_case_id_is_not_the_other_queues_case(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """The two queues share an id, which is what makes the test above meaningful."""
    sign_in(client, REVIEWER_EMAIL)

    assert only_case_id(client, tenants.organization_a, tenants.queue_a1) == only_case_id(
        client, tenants.organization_a, tenants.queue_a2
    )


def test_list_totals_are_queue_local(tenants: Tenants, client: TestClient) -> None:
    """Each queue holds one case, and neither counts the other's."""
    sign_in(client, REVIEWER_EMAIL)

    for queue in (tenants.queue_a1, tenants.queue_a2):
        page = client.get(cases_url(tenants.organization_a, queue)).json()
        assert page["total"] == 1
        assert page["count"] == 1
        assert len(page["items"]) == 1


# --------------------------------------------------------------------------
# REVIEWER: read, decide, and the identity that gets recorded
# --------------------------------------------------------------------------


def test_a_reviewer_can_read_and_decide(tenants: Tenants, client: TestClient) -> None:
    """The full working path, through the production application."""
    sign_in(client, REVIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)

    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)
    detail = client.get(f"{base}/{case_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == ReviewStatus.PENDING.value
    version = detail.json()["version"]

    resolved = client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": version},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert resolved.status_code == 200, resolved.status_code
    assert resolved.json()["case"]["status"] == "NO_MATCH"
    assert resolved.json()["case"]["version"] == version + 1


def test_the_durable_event_names_the_authenticated_user(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """The reviewer identity test, read back from the append-only history.

    The request body could not carry a reviewer id, so the value stored here can
    only have come from the session. It is compared against the user id the
    session endpoint reports -- the server's own answer to "who is this" -- so
    the assertion does not depend on anything the test made up.
    """
    sign_in(client, REVIEWER_EMAIL)
    user_id = client.get("/api/v1/auth/session").json()["user"]["user_id"]
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    events = client.get(f"{base}/{case_id}/events").json()

    resolutions = [event for event in events if event["is_resolution"]]
    assert [event["reviewer_id"] for event in resolutions] == [user_id]


def test_a_client_cannot_sign_a_decision_as_someone_else(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """The spoofing attempt, against the real endpoint, refused before the domain."""
    sign_in(client, REVIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    response = client.post(
        f"{base}/{case_id}/resolve",
        json={
            "decision": "NO_MATCH",
            "expected_version": 1,
            "reviewer_id": "USR-somebody-else",
        },
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    # And nothing was written.
    assert client.get(f"{base}/{case_id}").json()["status"] == ReviewStatus.PENDING.value


def test_the_decision_survives_a_restart(tenants: Tenants, client: TestClient) -> None:
    """A second application over the same file, with everything in between closed."""
    sign_in(client, REVIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)
    user_id = client.get("/api/v1/auth/session").json()["user"]["user_id"]
    client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    with TestClient(create_production_app()) as reopened:
        sign_in(reopened, REVIEWER_EMAIL)
        detail = reopened.get(f"{base}/{case_id}").json()
        events = reopened.get(f"{base}/{case_id}/events").json()

    assert detail["status"] == "NO_MATCH"
    assert detail["resolution"]["reviewer_id"] == user_id
    assert [event["reviewer_id"] for event in events if event["is_resolution"]] == [user_id]


# --------------------------------------------------------------------------
# VIEWER, non-member, and the wrong tenant
# --------------------------------------------------------------------------


def test_a_viewer_reads_the_queue(tenants: Tenants, client: TestClient) -> None:
    sign_in(client, VIEWER_EMAIL)

    response = client.get(cases_url(tenants.organization_a, tenants.queue_a1))

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_a_viewer_cannot_decide(tenants: Tenants, client: TestClient) -> None:
    """403, not 404: a VIEWER has already proven the queue exists by reading it."""
    sign_in(client, VIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    response = client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    # Still pending: the refusal happened before the application layer.
    assert client.get(f"{base}/{case_id}").json()["status"] == ReviewStatus.PENDING.value


def test_a_non_member_sees_nothing(tenants: Tenants, client: TestClient) -> None:
    """A real account with no membership learns only that there is nothing here."""
    sign_in(client, OUTSIDER_EMAIL)

    response = client.get(cases_url(tenants.organization_a, tenants.queue_a1))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_member_of_one_organization_cannot_reach_another(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """Organization B exists, holds data, and is invisible to A's reviewer."""
    sign_in(client, REVIEWER_EMAIL)

    response = client.get(cases_url(tenants.organization_b, tenants.queue_b1))

    assert response.status_code == 404
    assert tenants.organization_b not in response.text
    assert tenants.queue_b1 not in response.text


def test_pairing_an_owned_organization_with_a_foreign_queue_is_refused(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """Never silently corrected to the queue's real owner.

    This is the attack the ownership check exists for: name an organization you
    belong to, and a queue you do not, and hope the server trusts one of the two.
    """
    sign_in(client, REVIEWER_EMAIL)

    response = client.get(cases_url(tenants.organization_a, tenants.queue_b1))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_same_case_id_in_two_queues_stays_separate(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """A1 and B1 were registered from the same CSV, so they share a case id.

    The reviewer belongs to A only. The id resolves in A1 and is absent from
    B1 -- not because the case is missing there, but because the tenant is.
    """
    sign_in(client, REVIEWER_EMAIL)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    mine = client.get(f"{cases_url(tenants.organization_a, tenants.queue_a1)}/{case_id}")
    theirs = client.get(f"{cases_url(tenants.organization_b, tenants.queue_b1)}/{case_id}")

    assert mine.status_code == 200
    assert mine.json()["review_case_id"] == case_id
    assert theirs.status_code == 404


def test_resolving_in_one_queue_does_not_touch_the_other(
    tenants: Tenants,
    client: TestClient,
) -> None:
    """The strongest form of the collision test: a write, not just a read.

    Organization B's copy of the same case id must still be PENDING afterwards,
    and it is checked by signing in as nobody -- the assertion is made through
    the database the next application sees, not through a client that could be
    reading a cached page.
    """
    sign_in(client, REVIEWER_EMAIL)
    base_a = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    resolved = client.post(
        f"{base_a}/{case_id}/resolve",
        json={"decision": "MATCH", "expected_version": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert resolved.status_code == 200, resolved.status_code

    connection = open_review_database(api_dependencies.load_review_persistence_config())
    try:
        from review_persistence.sqlite import SqliteReviewCaseRepository

        in_b = SqliteReviewCaseRepository(connection, review_queue_id=tenants.queue_b1).get_case(
            case_id
        )
    finally:
        connection.close()

    assert in_b.status is ReviewStatus.PENDING
    assert in_b.version == 1


# --------------------------------------------------------------------------
# Unauthenticated, forged, and public
# --------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "/RC-anything", "/RC-anything/events"])
def test_an_unauthenticated_read_is_401(
    tenants: Tenants,
    client: TestClient,
    suffix: str,
) -> None:
    """No cookie, no queue -- and the same generic body every other refusal uses."""
    response = client.get(f"{cases_url(tenants.organization_a, tenants.queue_a1)}{suffix}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_a_forged_origin_cannot_resolve(tenants: Tenants, client: TestClient) -> None:
    sign_in(client, REVIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    response = client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
        headers={"Origin": FOREIGN_ORIGIN},
    )

    assert response.status_code == 403
    assert client.get(f"{base}/{case_id}").json()["status"] == ReviewStatus.PENDING.value


def test_a_missing_origin_cannot_resolve(tenants: Tenants, client: TestClient) -> None:
    """Absence is refused, not waved through. A caller that sends no Origin is
    not the browser these routes are built for."""
    sign_in(client, REVIEWER_EMAIL)
    base = cases_url(tenants.organization_a, tenants.queue_a1)
    case_id = only_case_id(client, tenants.organization_a, tenants.queue_a1)

    response = client.post(
        f"{base}/{case_id}/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
    )

    assert response.status_code == 403
    assert client.get(f"{base}/{case_id}").json()["status"] == ReviewStatus.PENDING.value


def test_health_is_still_public(tenants: Tenants, client: TestClient) -> None:
    """A liveness probe that needed a credential would need one to be issued."""
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/review-cases",
        "/api/v1/review-cases/RC-anything",
        "/api/v1/review-cases/RC-anything/events",
        "/api/v1/review-cases/RC-anything/semantic-suggestions",
    ],
)
def test_the_old_unscoped_routes_are_gone(
    tenants: Tenants,
    client: TestClient,
    path: str,
) -> None:
    """Not merely authenticated -- absent. Checked while signed in, because a
    route that still existed behind a session would answer 200 here."""
    sign_in(client, REVIEWER_EMAIL)

    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_old_unscoped_resolve_route_is_gone(tenants: Tenants, client: TestClient) -> None:
    sign_in(client, REVIEWER_EMAIL)

    response = client.post(
        "/api/v1/review-cases/RC-anything/resolve",
        json={"decision": "NO_MATCH", "expected_version": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Configuration integration, and the queue that must never be created
# --------------------------------------------------------------------------


def test_the_bootstrap_and_runtime_defaults_are_one_database(
    tenants: Tenants,
    configured: Path,
) -> None:
    """Compare resolved paths, not strings, through both production seams.

    ``configured_database_path`` is what the runner checks before starting, and
    the CLI's loader is what the bootstrap command opens. They must name the
    same file, or an operator bootstraps one queue and serves another.
    """
    bootstrap_target = manage_human_review.load_review_persistence_config().database_path
    runtime_target = api_dependencies.configured_database_path()

    assert bootstrap_target.resolve() == runtime_target.resolve() == configured.resolve()
    assert configured.exists()


def test_an_empty_queue_is_readable_and_cannot_decide(
    monkeypatch: pytest.MonkeyPatch,
    configured: Path,
) -> None:
    """An existing queue with no registered workflow: honest, and fail-closed.

    This is the residual state no startup check can detect -- the queue is real
    and owned by a real organization, it simply has no workflow authorization
    context yet. Reads answer an empty page, and any attempt to decide is
    refused with 503 rather than evaluated against a graph that was never
    stored.
    """
    assert (
        run_command(
            monkeypatch, "create-organization", "--slug", ORGANIZATION_A, "--name", ORGANIZATION_A
        )
        == 0
    )
    assert (
        run_command(
            monkeypatch, "create-review-queue", "--organization", ORGANIZATION_A, "--name", "empty"
        )
        == 0
    )
    create_user(monkeypatch, REVIEWER_EMAIL)
    assert (
        run_command(
            monkeypatch,
            "add-membership",
            "--user",
            REVIEWER_EMAIL,
            "--organization",
            ORGANIZATION_A,
            "--role",
            "REVIEWER",
        )
        == 0
    )
    ids = read_tenants_for_empty(configured)

    with TestClient(create_production_app()) as client:
        sign_in(client, REVIEWER_EMAIL)
        base = cases_url(*ids)
        assert client.get(base).json()["total"] == 0

        refused = client.post(
            f"{base}/RC-does-not-matter/resolve",
            json={"decision": "NO_MATCH", "expected_version": 1},
            headers={"Origin": ALLOWED_ORIGIN},
        )

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "REVIEW_QUEUE_NOT_READY"


def read_tenants_for_empty(database: Path) -> tuple[str, str]:
    connection = open_review_database(
        ReviewPersistenceConfig(database_path=database, busy_timeout_ms=2000, journal_mode="WAL")
    )
    try:
        tenants = SqliteTenantRepository(connection)
        organization = tenants.get_organization_by_slug(ORGANIZATION_A)
        assert organization is not None
        queue = tenants.get_review_queue_by_name(
            organization_id=organization.organization_id, name="empty"
        )
        assert queue is not None
        return organization.organization_id, queue.review_queue_id
    finally:
        connection.close()


def test_a_database_with_no_queue_at_all_still_starts(configured: Path) -> None:
    """Nothing to serve is not a reason to refuse to serve.

    The previous phase raised at startup here, because it had to bind a queue
    before it could answer anything. Nothing binds a queue now, so an empty
    installation starts and answers 401 to every review request -- which is the
    honest answer, and the same one it gives a stranger.
    """
    with TestClient(create_production_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get(cases_url("ORG-nothing", "RQ-nothing")).status_code == 401


def test_the_production_database_is_never_created() -> None:
    """Guards the fixture wiring for this module."""
    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
