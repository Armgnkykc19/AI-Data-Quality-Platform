"""Resolution over HTTP against the real Sprint 08 + Sprint 10 stack.

The fake-service tests prove the transport contract. This file proves the thing
that actually matters: that putting an HTTP boundary in front of
``ReviewQueueService`` did not weaken Sprint 08 authorization.

Two properties carry the weight.

Authorization is component-wide, not pairwise. A MATCH that is safe for the two
records under review can be unsafe because AUTO_MATCH edges pull conflicting
records into the same component, or because a human already recorded a NO_MATCH
somewhere in it. Both are tested through the endpoint, and the severe-conflict
test has a negative control: the same reviewed pair is *allowed* once the
AUTO_MATCH edges are gone. Without that control the test would still pass if the
API had broken authorization entirely and refused everything.

Nothing is written before the domain approves. Every refusal below is followed
by a durable-state comparison read back on a fresh connection.

Every database lives under ``tmp_path``; ``storage/review_queue.db`` is never
opened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from entity_resolution.config import load_entity_resolution_config
from human_review.cases import generate_review_cases
from human_review.reporting import resolution_snapshot
from review_api import create_app
from review_application import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite import SqliteReviewCaseRepository, open_review_database
from tests.human_review.conftest import (
    make_bridge_resolution,
    make_record,
    make_review_resolution,
    make_triangle_review_resolution,
)
from tests.review_api.conftest import NOW, records_by_id
from tests.review_persistence.semantic_fixtures import make_suggestion

BASE_URL = "/api/v1/review-cases"
ER_CONFIG_PATH = "configs/entity_resolution.yaml"


def resolve_url(case_id: str) -> str:
    return f"{BASE_URL}/{case_id}/resolve"


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Queue:
    """A seeded review queue on disk, plus the ids a test needs to address it."""

    config: ReviewPersistenceConfig
    case_ids_by_pair: dict[tuple[str, str], str]

    @property
    def only_case_id(self) -> str:
        assert len(self.case_ids_by_pair) == 1
        return next(iter(self.case_ids_by_pair.values()))


@contextmanager
def open_repository(config: ReviewPersistenceConfig) -> Iterator[SqliteReviewCaseRepository]:
    """A short-lived repository on the calling thread, for seeding and verifying."""
    database = open_review_database(config)
    try:
        yield SqliteReviewCaseRepository(database)
    finally:
        database.close()


def seed(
    tmp_path: Path,
    resolution,
    *,
    snapshot: dict | None = None,
    config_path: str | None = ER_CONFIG_PATH,
    name: str = "review_queue.db",
) -> Queue:
    """Register a real generated workflow into a temporary database.

    ``snapshot`` overrides the persisted AUTO_MATCH reduction. It exists for the
    negative control: registering the same cases with no AUTO_MATCH edges is
    exactly the state a queue would be in if those edges had never existed.
    """
    persistence = ReviewPersistenceConfig(
        database_path=tmp_path / name, busy_timeout_ms=2000, journal_mode="WAL"
    )
    state = generate_review_cases(resolution, config=load_entity_resolution_config())
    with open_repository(persistence) as repository:
        repository.register_workflow(
            state,
            entity_records=resolution.records,
            resolution_snapshot=(
                snapshot if snapshot is not None else resolution_snapshot(resolution)
            ),
            entity_resolution_config_path=config_path,
            now_utc=NOW,
        )
    return Queue(
        config=persistence,
        case_ids_by_pair={
            (case.pair.record_a_id, case.pair.record_b_id): case.review_case_id
            for case in state.cases
        },
    )


@asynccontextmanager
async def queue_lifespan(app: FastAPI, config: ReviewPersistenceConfig) -> AsyncIterator[None]:
    """Mirror ``production_lifespan`` against a temporary database.

    The connection is opened here, inside the lifespan, because a ``sqlite3``
    connection is legal only on its creating thread and ``TestClient`` serves on
    its own. This is the Phase A runtime constraint, unchanged: no
    ``check_same_thread``, no lock, no executor. Sprint 14 owns the redesign.
    """
    database = open_review_database(config)
    try:
        repository = SqliteReviewCaseRepository(database)
        app.state.repository = repository
        app.state.service = ReviewQueueService(repository)
        yield
    finally:
        app.state.repository = None
        app.state.service = None
        database.close()


@contextmanager
def api(config: ReviewPersistenceConfig) -> Iterator[TestClient]:
    def lifespan(app: FastAPI):
        return queue_lifespan(app, config)

    with TestClient(create_app(lifespan=lifespan)) as client:
        yield client


# --------------------------------------------------------------------------
# Durable state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DurableState:
    status: str
    version: int
    created_at_utc: str
    updated_at_utc: str
    event_count: int
    resolution_event_count: int
    suggestion_count: int
    has_resolution: bool


def durable_state(config: ReviewPersistenceConfig, case_id: str) -> DurableState:
    """Read committed state back on a fresh connection, not from a cached object."""
    with open_repository(config) as repository:
        case = repository.get_case(case_id)
        events = repository.list_events(case_id)
        return DurableState(
            status=case.status.value,
            version=case.version,
            created_at_utc=case.created_at_utc,
            updated_at_utc=case.updated_at_utc,
            event_count=len(events),
            resolution_event_count=sum(1 for event in events if event.is_resolution),
            suggestion_count=len(repository.list_semantic_suggestions(case_id)),
            has_resolution=case.case.resolution is not None,
        )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def edge_dependent_bridge():
    """Only the AUTO_MATCH-reachable records conflict with each other.

    rec-b and rec-c carry no e-mail, so the reviewed pair is safe in isolation.
    rec-a and rec-d conflict, and they are in the component only because the two
    AUTO_MATCH edges put them there. The edges alone decide the verdict, which
    is what makes the negative control below meaningful.

    Borrowed from the Sprint 10 authorization-readiness tests rather than
    reinvented; the API must not carry its own copy of an authorization fixture.
    """
    records = (
        make_record("rec-a", first_name="Ali", last_name="Yilmaz", email="a@example.com"),
        make_record("rec-b", first_name="Ali", last_name="Yilmaz"),
        make_record("rec-c", first_name="Ali", last_name="Yilmaz"),
        make_record("rec-d", first_name="Ali", last_name="Yilmaz", email="d@example.com"),
    )
    return make_bridge_resolution(
        left_ids=("rec-a", "rec-b"),
        right_ids=("rec-c", "rec-d"),
        bridge_ids=("rec-b", "rec-c"),
        records=records,
    )


@pytest.fixture
def simple_queue(tmp_path: Path) -> Queue:
    """One safe PENDING case whose MATCH Sprint 08 permits."""
    return seed(tmp_path, make_review_resolution("a-1", "a-2"))


@pytest.fixture
def bridge_queue(tmp_path: Path) -> Queue:
    """The edge-dependent severe conflict, with its AUTO_MATCH edges persisted."""
    return seed(tmp_path, edge_dependent_bridge())


@pytest.fixture
def bridge_queue_without_edges(tmp_path: Path) -> Queue:
    """The same reviewed pair, with the AUTO_MATCH reduction emptied."""
    return seed(
        tmp_path,
        edge_dependent_bridge(),
        snapshot={"source_label": "no-auto-match-edges", "auto_match_pairs": []},
        name="control.db",
    )


@pytest.fixture
def triangle_queue(tmp_path: Path) -> Queue:
    """Three mutually reviewable records, for the transitive NO_MATCH constraint."""
    return seed(tmp_path, make_triangle_review_resolution(("rec-a", "rec-b", "rec-c")))


# --------------------------------------------------------------------------
# Severe conflict: refused, and the control that proves it is not vacuous
# --------------------------------------------------------------------------


def test_an_edge_dependent_severe_conflict_is_refused(bridge_queue: Queue) -> None:
    """The reviewed pair is safe alone and unsafe in its component.

    If the HTTP layer had narrowed authorization to the requested pair -- by
    fetching one case, or by letting a client supply the graph -- this would
    succeed. It is the single most important assertion in Phase C.
    """
    case_id = bridge_queue.only_case_id

    with api(bridge_queue.config) as client:
        response = client.post(
            resolve_url(case_id),
            json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-1"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MATCH_NOT_AUTHORIZED"


def test_the_same_pair_is_allowed_without_the_auto_match_edges(
    bridge_queue_without_edges: Queue,
) -> None:
    """Negative control. Without it the test above would pass on a broken API.

    Same records, same reviewed pair, same request -- only the persisted
    AUTO_MATCH reduction differs. The refusal above is therefore caused by the
    edges surviving the HTTP boundary, not by the endpoint refusing everything.
    """
    case_id = bridge_queue_without_edges.only_case_id

    with api(bridge_queue_without_edges.config) as client:
        response = client.post(
            resolve_url(case_id),
            json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-1"},
        )

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "MATCH"


def test_a_refused_match_writes_nothing(bridge_queue: Queue) -> None:
    case_id = bridge_queue.only_case_id
    before = durable_state(bridge_queue.config, case_id)

    with api(bridge_queue.config) as client:
        assert (
            client.post(
                resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1}
            ).status_code
            == 422
        )

    assert durable_state(bridge_queue.config, case_id) == before
    assert before.status == "PENDING"
    assert before.version == 1
    assert before.resolution_event_count == 0
    assert not before.has_resolution


def test_a_refused_match_can_be_retried_as_no_match(bridge_queue: Queue) -> None:
    """The case is untouched by the refusal, so the safe decision still works."""
    case_id = bridge_queue.only_case_id

    with api(bridge_queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        response = client.post(
            resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": 1}
        )

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "NO_MATCH"
    assert response.json()["case"]["version"] == 2


# --------------------------------------------------------------------------
# Transitive human NO_MATCH
# --------------------------------------------------------------------------


def test_a_transitive_no_match_constraint_survives_the_http_boundary(
    triangle_queue: Queue,
) -> None:
    """A human NO_MATCH on (a,c) forbids the MATCH chain that would reunite them.

    Every step goes through the endpoint. The final MATCH is refused not because
    of anything about rec-b and rec-c, but because merging them would place the
    already-refused pair in one component -- a conclusion reachable only from
    the complete persisted queue.
    """
    ids = triangle_queue.case_ids_by_pair

    with api(triangle_queue.config) as client:
        assert (
            client.post(
                resolve_url(ids[("rec-a", "rec-c")]),
                json={"decision": "NO_MATCH", "expected_version": 1, "reviewer_id": "rev-1"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                resolve_url(ids[("rec-a", "rec-b")]),
                json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-1"},
            ).status_code
            == 200
        )

        forbidden = client.post(
            resolve_url(ids[("rec-b", "rec-c")]),
            json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-1"},
        )

    assert forbidden.status_code == 409
    assert forbidden.json()["error"]["code"] == "HUMAN_REVIEW_CONTRADICTION"


def test_the_contradicting_match_writes_nothing(triangle_queue: Queue) -> None:
    ids = triangle_queue.case_ids_by_pair
    blocked = ids[("rec-b", "rec-c")]

    with api(triangle_queue.config) as client:
        client.post(
            resolve_url(ids[("rec-a", "rec-c")]),
            json={"decision": "NO_MATCH", "expected_version": 1},
        )
        client.post(
            resolve_url(ids[("rec-a", "rec-b")]), json={"decision": "MATCH", "expected_version": 1}
        )
        before = durable_state(triangle_queue.config, blocked)
        client.post(resolve_url(blocked), json={"decision": "MATCH", "expected_version": 1})

    assert durable_state(triangle_queue.config, blocked) == before
    assert before.status == "PENDING"
    assert before.version == 1


def test_no_authorization_detail_leaks_from_a_real_refusal(bridge_queue: Queue) -> None:
    """The refusal is real, so the message it suppresses is a real one."""
    case_id = bridge_queue.only_case_id

    with api(bridge_queue.config) as client:
        body = client.post(
            resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1}
        ).text

    for token in (
        "rec-a",
        "rec-d",
        "a@example.com",
        "d@example.com",
        "component",
        "severe",
        "conflict",
        "email",
        "auto_match",
        "0.88",
        "threshold",
        "configs/",
        ".yaml",
        "Traceback",
    ):
        assert token not in body, f"{token} leaked from an authorization refusal"


# --------------------------------------------------------------------------
# Success paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_event"),
    [
        ("MATCH", "MATCH", "MATCH"),
        ("NO_MATCH", "NO_MATCH", "NO_MATCH"),
        ("DEFER", "DEFERRED", "DEFERRED"),
    ],
)
def test_each_decision_transitions_the_case(
    simple_queue: Queue,
    decision: str,
    expected_status: str,
    expected_event: str,
) -> None:
    """DEFER is the decision; DEFERRED is the status. The API keeps them distinct."""
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        version = client.get(f"{BASE_URL}/{case_id}").json()["version"]
        response = client.post(
            resolve_url(case_id),
            json={"decision": decision, "expected_version": version, "reviewer_id": "rev-9"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["case"]["status"] == expected_status
    assert body["case"]["resolution"]["human_decision"] == decision
    assert body["case"]["resolution"]["reviewer_id"] == "rev-9"
    assert body["event"]["event_type"] == expected_event
    assert body["event"]["is_resolution"] is True


def test_a_successful_resolution_increments_the_version_exactly_once(
    simple_queue: Queue,
) -> None:
    case_id = simple_queue.only_case_id
    before = durable_state(simple_queue.config, case_id)

    with api(simple_queue.config) as client:
        body = client.post(
            resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1}
        ).json()

    after = durable_state(simple_queue.config, case_id)
    assert before.version == 1
    assert after.version == 2
    assert body["case"]["version"] == 2


def test_a_successful_resolution_appends_exactly_one_resolution_event(
    simple_queue: Queue,
) -> None:
    case_id = simple_queue.only_case_id
    before = durable_state(simple_queue.config, case_id)

    with api(simple_queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": 1})
        events = client.get(f"{BASE_URL}/{case_id}/events").json()

    after = durable_state(simple_queue.config, case_id)
    assert after.resolution_event_count == before.resolution_event_count + 1
    resolutions = [event for event in events if event["is_resolution"]]
    assert len(resolutions) == 1
    assert resolutions[0]["event_type"] == "NO_MATCH"
    assert resolutions[0]["resolution_sequence"] == 1


def test_the_appended_event_has_an_id_when_read_back(simple_queue: Queue) -> None:
    """The response reports ``event_id: null``; the history endpoint has the id.

    The service hands storage an event before the database assigns its id, and
    ``apply_resolution`` returns only the updated case. This pins both halves so
    the difference is a documented contract rather than a surprise.
    """
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        posted = client.post(
            resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1}
        ).json()
        events = client.get(f"{BASE_URL}/{case_id}/events").json()

    assert posted["event"]["event_id"] is None
    stored = [event for event in events if event["is_resolution"]]
    assert stored[0]["event_id"] is not None


def test_the_resolution_survives_a_restart(simple_queue: Queue) -> None:
    """A second application over the same file, with everything in between closed."""
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        client.post(
            resolve_url(case_id),
            json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "rev-restart"},
        )

    with api(simple_queue.config) as reopened:
        detail = reopened.get(f"{BASE_URL}/{case_id}").json()
        events = reopened.get(f"{BASE_URL}/{case_id}/events").json()

    assert detail["status"] == "MATCH"
    assert detail["version"] == 2
    assert detail["resolution"]["reviewer_id"] == "rev-restart"
    assert [event["event_type"] for event in events if event["is_resolution"]] == ["MATCH"]


def test_a_resolution_leaves_semantic_suggestions_untouched(tmp_path: Path) -> None:
    """Advisory stays advisory: a human decision neither reads nor rewrites it."""
    resolution = make_review_resolution("a-1", "a-2")
    queue = seed(tmp_path, resolution)
    case_id = queue.only_case_id

    state = generate_review_cases(resolution, config=load_entity_resolution_config())
    suggestion = make_suggestion(state.cases[0], records_by_id(resolution))
    with open_repository(queue.config) as repository:
        repository.record_semantic_suggestion(suggestion, now_utc=NOW)

    before = durable_state(queue.config, case_id)

    with api(queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        suggestions = client.get(f"{BASE_URL}/{case_id}/semantic-suggestions").json()

    after = durable_state(queue.config, case_id)
    assert before.suggestion_count == 1
    assert after.suggestion_count == 1
    assert len(suggestions) == 1
    assert suggestions[0]["suggestion_id"] == suggestion.suggestion_id
    assert suggestions[0]["advisory"] is True
    assert "explanation" not in suggestions[0]


def test_a_semantic_event_does_not_consume_a_resolution_sequence(tmp_path: Path) -> None:
    resolution = make_review_resolution("a-1", "a-2")
    queue = seed(tmp_path, resolution)
    case_id = queue.only_case_id
    state = generate_review_cases(resolution, config=load_entity_resolution_config())
    with open_repository(queue.config) as repository:
        repository.record_semantic_suggestion(
            make_suggestion(state.cases[0], records_by_id(resolution)), now_utc=NOW
        )

    with api(queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        events = client.get(f"{BASE_URL}/{case_id}/events").json()

    semantic = [e for e in events if e["event_type"] == "SEMANTIC_SUGGESTION_RECORDED"]
    resolutions = [e for e in events if e["is_resolution"]]
    assert semantic and semantic[0]["resolution_sequence"] is None
    assert [e["resolution_sequence"] for e in resolutions] == [1]


# --------------------------------------------------------------------------
# Concurrency and terminal state
# --------------------------------------------------------------------------


def test_two_reviewers_racing_one_case(simple_queue: Queue) -> None:
    """Both load version 1; the second to decide loses.

    Run against the real stack rather than a fake, because the guarantee is
    partly the service's version check and partly the SQLite conditional update.
    A fake would only ever exercise the half that lives in Python.
    """
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        reviewer_a_version = client.get(f"{BASE_URL}/{case_id}").json()["version"]
        reviewer_b_version = client.get(f"{BASE_URL}/{case_id}").json()["version"]
        assert reviewer_a_version == reviewer_b_version == 1

        first = client.post(
            resolve_url(case_id),
            json={"decision": "MATCH", "expected_version": reviewer_a_version, "reviewer_id": "A"},
        )
        second = client.post(
            resolve_url(case_id),
            json={
                "decision": "NO_MATCH",
                "expected_version": reviewer_b_version,
                "reviewer_id": "B",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "REVIEW_CASE_VERSION_CONFLICT"
    assert second.json()["error"]["details"] == {"expected_version": 1}


def test_the_loser_of_a_race_changes_nothing(simple_queue: Queue) -> None:
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        client.post(
            resolve_url(case_id),
            json={"decision": "MATCH", "expected_version": 1, "reviewer_id": "A"},
        )
        after_winner = durable_state(simple_queue.config, case_id)

        client.post(
            resolve_url(case_id),
            json={"decision": "NO_MATCH", "expected_version": 1, "reviewer_id": "B"},
        )
        after_loser = durable_state(simple_queue.config, case_id)

        detail = client.get(f"{BASE_URL}/{case_id}").json()
        events = client.get(f"{BASE_URL}/{case_id}/events").json()

    assert after_loser == after_winner
    assert after_winner.version == 2
    assert after_winner.resolution_event_count == 1
    assert detail["status"] == "MATCH"
    assert detail["resolution"]["reviewer_id"] == "A"
    assert [e["reviewer_id"] for e in events if e["is_resolution"]] == ["A"]


def test_resolving_a_terminal_case_at_its_current_version_is_a_different_conflict(
    simple_queue: Queue,
) -> None:
    """Uses the *current* version deliberately.

    With a stale version the service stops at the staleness check and answers
    REVIEW_CASE_VERSION_CONFLICT, which would hide the transition rule entirely.
    Supplying the current version gets past that check so the domain is the one
    that refuses, and the client learns the case is terminal rather than stale.
    """
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        current = client.get(f"{BASE_URL}/{case_id}").json()["version"]
        assert current == 2

        second = client.post(
            resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": current}
        )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "REVIEW_CASE_NOT_PENDING"


def test_the_two_conflict_codes_are_distinguishable(simple_queue: Queue) -> None:
    """Stale and terminal are different problems with different remedies."""
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        stale = client.post(
            resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": 1}
        )
        terminal = client.post(
            resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": 2}
        )

    assert stale.json()["error"]["code"] == "REVIEW_CASE_VERSION_CONFLICT"
    assert terminal.json()["error"]["code"] == "REVIEW_CASE_NOT_PENDING"
    assert stale.status_code == terminal.status_code == 409


def test_a_second_resolution_appends_no_second_event(simple_queue: Queue) -> None:
    case_id = simple_queue.only_case_id

    with api(simple_queue.config) as client:
        client.post(resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1})
        after_first = durable_state(simple_queue.config, case_id)
        client.post(resolve_url(case_id), json={"decision": "NO_MATCH", "expected_version": 2})

    assert durable_state(simple_queue.config, case_id) == after_first
    assert after_first.resolution_event_count == 1


# --------------------------------------------------------------------------
# Missing server-side material
# --------------------------------------------------------------------------


def test_a_queue_without_an_er_config_path_cannot_authorize_a_match(tmp_path: Path) -> None:
    """503, not 422: nothing evaluated the merge, so nothing refused it.

    Reporting this as MATCH_NOT_AUTHORIZED would tell a reviewer their decision
    is unsafe when the server merely lacks the material to judge it.
    """
    queue = seed(tmp_path, make_review_resolution("a-1", "a-2"), config_path=None)
    case_id = queue.only_case_id
    before = durable_state(queue.config, case_id)

    with api(queue.config) as client:
        response = client.post(
            resolve_url(case_id), json={"decision": "MATCH", "expected_version": 1}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHORIZATION_CONTEXT_UNAVAILABLE"
    assert durable_state(queue.config, case_id) == before


def test_the_same_queue_still_accepts_a_no_match(tmp_path: Path) -> None:
    """Only MATCH needs the authorization context; the safe decision is unaffected.

    Blocking a reviewer from recording a refusal because of a problem that
    cannot affect it would be the wrong kind of fail-closed.
    """
    queue = seed(tmp_path, make_review_resolution("a-1", "a-2"), config_path=None)

    with api(queue.config) as client:
        response = client.post(
            resolve_url(queue.only_case_id),
            json={"decision": "NO_MATCH", "expected_version": 1},
        )

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "NO_MATCH"


def test_an_unknown_case_is_a_404(simple_queue: Queue) -> None:
    with api(simple_queue.config) as client:
        response = client.post(
            resolve_url("RC-0000000000000000"),
            json={"decision": "MATCH", "expected_version": 1},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVIEW_CASE_NOT_FOUND"


def test_the_production_database_is_never_created() -> None:
    """Guards the fixture wiring: every queue here is an explicit tmp_path config."""
    from review_persistence.config import PROJECT_ROOT

    assert not (PROJECT_ROOT / "storage" / "review_queue.db").exists()
