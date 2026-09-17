"""The four read endpoints, against a repository that cannot write.

Every test here runs on ``FakeReviewCaseRepository``, whose authority methods
raise ``AssertionError``. So "this GET has no side effects" is not a separate
assertion bolted on at the end -- it is a property of every test in the file. A
route that resolved, registered, or recorded anything would fail here.

The cases themselves are real: produced by ``generate_review_cases`` and, where
resolved, by ``ReviewWorkflow.resolve_case``. Projecting a hand-built lookalike
would prove the mapper works on the shape the test author imagined.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from human_review.models import HumanReviewDecision, ReviewStatus
from review_application import PersistedCase, ReviewEvent
from tests.review_api.conftest import (
    LATER,
    NOW,
    api_client,
    build_case,
    persist,
    records_by_id,
    resolution_event,
)
from tests.review_api.fake_repository import FakeReviewCaseRepository
from tests.review_persistence.semantic_fixtures import as_provider_failure, make_suggestion

CASES_URL = "/api/v1/review-cases"

SUMMARY_FIELDS = {
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "status",
    "machine_decision",
    "machine_score",
    "version",
    "created_at_utc",
    "updated_at_utc",
}

DETAIL_FIELDS = SUMMARY_FIELDS | {
    "auto_match_threshold",
    "review_threshold",
    "machine_reason",
    "human_summary",
    "missing_evidence_notes",
    "blocking_reasons",
    "supporting_evidence",
    "conflicting_evidence",
    "resolution",
}

EVENT_FIELDS = {
    "event_id",
    "event_type",
    "occurred_at_utc",
    "resolution_sequence",
    "reviewer_id",
    "suggestion_id",
    "is_resolution",
}

SUGGESTION_FIELDS = {
    "suggestion_id",
    "suggestion",
    "reason_codes",
    "provider",
    "requested_model",
    "failure_code",
    "live",
    "created_at_utc",
    "advisory",
}


@pytest.fixture
def repository(queue: tuple[PersistedCase, ...]) -> FakeReviewCaseRepository:
    return FakeReviewCaseRepository(cases=queue)


@pytest.fixture
def client(repository: FakeReviewCaseRepository) -> TestClient:
    return api_client(repository)


# --------------------------------------------------------------------------
# List: filtering
# --------------------------------------------------------------------------


def test_list_returns_every_case_without_a_filter(client: TestClient) -> None:
    body = client.get(CASES_URL).json()

    assert body["total"] == 4
    assert body["count"] == 4
    assert len(body["items"]) == 4


@pytest.mark.parametrize(
    "status",
    [ReviewStatus.PENDING, ReviewStatus.MATCH, ReviewStatus.NO_MATCH, ReviewStatus.DEFERRED],
)
def test_list_filters_by_each_status(client: TestClient, status: ReviewStatus) -> None:
    body = client.get(CASES_URL, params={"status": status.value}).json()

    assert body["total"] == 1
    assert [item["status"] for item in body["items"]] == [status.value]


def test_the_repository_receives_the_enum_not_a_string(
    client: TestClient,
    repository: FakeReviewCaseRepository,
) -> None:
    """The route must not hand a raw query string to the storage layer."""
    client.get(CASES_URL, params={"status": "PENDING"})

    assert repository.list_cases_calls == [ReviewStatus.PENDING]
    assert isinstance(repository.list_cases_calls[0], ReviewStatus)


def test_no_filter_passes_none(client: TestClient, repository: FakeReviewCaseRepository) -> None:
    client.get(CASES_URL)

    assert repository.list_cases_calls == [None]


def test_lowercase_status_is_rejected(client: TestClient) -> None:
    """Strict by design: relabelling a status is the domain's job, not the API's."""
    response = client.get(CASES_URL, params={"status": "pending"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_an_unknown_status_value_is_not_echoed(client: TestClient) -> None:
    response = client.get(CASES_URL, params={"status": "SENTINEL-BAD-STATUS-77"})

    assert response.status_code == 422
    assert "SENTINEL-BAD-STATUS-77" not in response.text


def test_a_filter_matching_nothing_is_an_empty_page(pending_case: PersistedCase) -> None:
    client = api_client(FakeReviewCaseRepository(cases=(pending_case,)))

    body = client.get(CASES_URL, params={"status": "MATCH"}).json()

    assert body["items"] == []
    assert body["total"] == 0
    assert body["count"] == 0


# --------------------------------------------------------------------------
# List: pagination
# --------------------------------------------------------------------------


def test_list_response_field_set_is_pinned(client: TestClient) -> None:
    body = client.get(CASES_URL).json()

    assert set(body) == {"items", "count", "total", "limit", "offset"}


def test_default_limit_and_offset(client: TestClient) -> None:
    body = client.get(CASES_URL).json()

    assert body["limit"] == 50
    assert body["offset"] == 0


def test_explicit_limit_slices_the_page(client: TestClient) -> None:
    body = client.get(CASES_URL, params={"limit": 2}).json()

    assert body["count"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 2


def test_total_counts_the_filtered_set_before_slicing(client: TestClient) -> None:
    """The distinction that makes a page navigable at all."""
    body = client.get(CASES_URL, params={"limit": 1}).json()

    assert body["count"] == 1
    assert body["total"] == 4


def test_offset_walks_the_queue(client: TestClient) -> None:
    everything = client.get(CASES_URL).json()["items"]

    page = client.get(CASES_URL, params={"limit": 2, "offset": 2}).json()

    assert page["items"] == everything[2:4]
    assert page["offset"] == 2


def test_offset_beyond_the_end_is_empty_but_still_reports_the_total(client: TestClient) -> None:
    body = client.get(CASES_URL, params={"offset": 99}).json()

    assert body["items"] == []
    assert body["count"] == 0
    assert body["total"] == 4


def test_count_always_equals_the_page_length(client: TestClient) -> None:
    for params in ({}, {"limit": 1}, {"limit": 3, "offset": 2}, {"offset": 99}):
        body = client.get(CASES_URL, params=params).json()
        assert body["count"] == len(body["items"])


def test_limit_200_is_accepted(client: TestClient) -> None:
    assert client.get(CASES_URL, params={"limit": 200}).status_code == 200


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"limit": -1}, {"offset": -1}])
def test_out_of_range_pagination_is_rejected(client: TestClient, params: dict) -> None:
    response = client.get(CASES_URL, params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_repository_ordering_is_preserved(
    client: TestClient,
    queue: tuple[PersistedCase, ...],
) -> None:
    """The API never re-sorts. Queue order is the repository's statement."""
    items = client.get(CASES_URL).json()["items"]

    assert [item["review_case_id"] for item in items] == [c.review_case_id for c in queue]


def test_pagination_happens_after_filtering(queue: tuple[PersistedCase, ...]) -> None:
    """Slicing the unfiltered set would return the wrong page for a filter."""
    client = api_client(FakeReviewCaseRepository(cases=queue))

    body = client.get(CASES_URL, params={"status": "MATCH", "offset": 0, "limit": 10}).json()

    assert body["total"] == 1
    assert body["items"][0]["status"] == "MATCH"


# --------------------------------------------------------------------------
# Summary contract
# --------------------------------------------------------------------------


def test_summary_field_set_is_pinned(client: TestClient) -> None:
    for item in client.get(CASES_URL).json()["items"]:
        assert set(item) == SUMMARY_FIELDS


def test_summary_carries_no_evidence(client: TestClient) -> None:
    """Evidence belongs to the decision screen, not to every queue row."""
    body = client.get(CASES_URL).text

    for token in ("blocking_reasons", "supporting_evidence", "conflicting_evidence"):
        assert token not in body


def test_summary_reports_the_concurrency_version(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    item = client.get(CASES_URL, params={"status": "PENDING"}).json()["items"][0]

    assert item["version"] == pending_case.version


# --------------------------------------------------------------------------
# Detail
# --------------------------------------------------------------------------


def test_detail_field_set_is_pinned(client: TestClient, pending_case: PersistedCase) -> None:
    body = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()

    assert set(body) == DETAIL_FIELDS


def test_detail_projects_the_real_case(client: TestClient, pending_case: PersistedCase) -> None:
    case = pending_case.case
    body = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()

    assert body["review_case_id"] == case.review_case_id
    assert body["record_a_id"] == case.pair.record_a_id
    assert body["record_b_id"] == case.pair.record_b_id
    assert body["status"] == "PENDING"
    assert body["machine_decision"] == case.machine_decision.value
    assert body["machine_score"] == case.machine_score
    assert body["machine_reason"] == case.machine_reason
    assert body["human_summary"] == case.human_summary
    assert body["version"] == pending_case.version
    assert body["created_at_utc"] == NOW
    assert body["resolution"] is None


def test_detail_publishes_the_thresholds_that_explain_the_score(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    """Without them, machine_score is a number with no frame of reference."""
    body = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()

    assert body["auto_match_threshold"] == pending_case.case.auto_match_threshold
    assert body["review_threshold"] == pending_case.case.review_threshold


@pytest.mark.parametrize(
    ("fixture_name", "expected_status", "expected_decision"),
    [
        ("resolved_match_case", "MATCH", "MATCH"),
        ("resolved_no_match_case", "NO_MATCH", "NO_MATCH"),
        ("deferred_case", "DEFERRED", "DEFER"),
    ],
)
def test_detail_projects_each_resolved_status(
    request: pytest.FixtureRequest,
    fixture_name: str,
    expected_status: str,
    expected_decision: str,
) -> None:
    """DEFER is the decision; DEFERRED is the resulting status. Both are published."""
    persisted: PersistedCase = request.getfixturevalue(fixture_name)
    client = api_client(FakeReviewCaseRepository(cases=(persisted,)))

    body = client.get(f"{CASES_URL}/{persisted.review_case_id}").json()

    assert body["status"] == expected_status
    assert body["resolution"]["human_decision"] == expected_decision
    assert body["version"] == 2
    assert body["updated_at_utc"] == LATER


def test_resolution_field_set_is_pinned(resolved_match_case: PersistedCase) -> None:
    client = api_client(FakeReviewCaseRepository(cases=(resolved_match_case,)))

    resolution = client.get(f"{CASES_URL}/{resolved_match_case.review_case_id}").json()[
        "resolution"
    ]

    assert set(resolution) == {
        "human_decision",
        "reviewer_id",
        "resolution_sequence",
        "downstream_action",
    }


def test_resolution_does_not_restate_the_case_level_machine_verdict(
    resolved_match_case: PersistedCase,
) -> None:
    """The domain copies these onto the resolution; the API publishes them once."""
    client = api_client(FakeReviewCaseRepository(cases=(resolved_match_case,)))

    body = client.get(f"{CASES_URL}/{resolved_match_case.review_case_id}").json()

    assert "machine_decision" not in body["resolution"]
    assert "machine_reason" not in body["resolution"]
    assert body["machine_decision"] == resolved_match_case.case.machine_decision.value


def test_resolution_comes_from_the_case_not_from_events(
    resolved_match_case: PersistedCase,
) -> None:
    """No event history is supplied, and the resolution is still correct."""
    client = api_client(FakeReviewCaseRepository(cases=(resolved_match_case,), events={}))

    resolution = client.get(f"{CASES_URL}/{resolved_match_case.review_case_id}").json()[
        "resolution"
    ]

    assert resolution["human_decision"] == "MATCH"
    assert resolution["reviewer_id"] == "rev-1"
    assert resolution["resolution_sequence"] == 1


def test_unknown_case_is_a_404(client: TestClient) -> None:
    response = client.get(f"{CASES_URL}/RC-doesnotexist0000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVIEW_CASE_NOT_FOUND"


def test_detail_exposes_no_internal_material(client: TestClient, queue) -> None:
    forbidden = (
        "schema_version",
        "case_payload_json",
        "audit_entry_payload",
        "entity_resolution_config_path",
        "resolution_snapshot",
        "auto_match_pairs",
        "entity_records",
        "workflow_context",
        "field_values",
        "source_name",
        "person_id",
        "record_ids",
        "next_resolution_sequence",
    )
    for persisted in queue:
        body = client.get(f"{CASES_URL}/{persisted.review_case_id}").text
        for token in forbidden:
            assert token not in body, f"{token} leaked for {persisted.review_case_id}"


# --------------------------------------------------------------------------
# Evidence and the blocking key
# --------------------------------------------------------------------------


def test_blocking_reason_field_set_is_pinned(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    reasons = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()["blocking_reasons"]

    assert reasons
    for reason in reasons:
        assert set(reason) == {"reason_type", "blocking_key"}


def test_the_blocking_key_is_published_exactly_once(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    """The core minimization of this phase.

    The key is customer-derived -- a normalized email, a phone fragment, a
    name+surname compound. Sprint 08 carries it in three places: the structured
    field, the prose description that interpolates it, and a
    ``blocking:<TYPE>:<key>`` entry in machine_readable_reasons. A reviewer
    needs it once.
    """
    key = pending_case.case.blocking_reasons[0].blocking_key
    assert key, "Fixture must produce a non-empty blocking key."

    body = client.get(f"{CASES_URL}/{pending_case.review_case_id}").text

    assert body.count(key) == 1


def test_machine_readable_reasons_are_not_published(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    """Fully derivable from published fields, and it carries the key again."""
    assert any(
        item.startswith("blocking:") for item in pending_case.case.machine_readable_reasons
    ), "Fixture must contain the blocking token this test exists to exclude."

    body = client.get(f"{CASES_URL}/{pending_case.review_case_id}").text

    assert "machine_readable_reasons" not in body
    assert "blocking:" not in body


def test_supporting_evidence_keeps_its_description(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    """Evidence prose names fields and scores, never record values."""
    evidence = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()[
        "supporting_evidence"
    ]

    assert evidence
    for item in evidence:
        assert set(item) == {
            "evidence_type",
            "field_name",
            "strength",
            "contribution",
            "description",
        }


def test_conflicting_evidence_contract(resolved_no_match_case: PersistedCase) -> None:
    client = api_client(FakeReviewCaseRepository(cases=(resolved_no_match_case,)))

    conflicts = client.get(f"{CASES_URL}/{resolved_no_match_case.review_case_id}").json()[
        "conflicting_evidence"
    ]

    for item in conflicts:
        assert set(item) == {"conflict_type", "field_name", "severity", "penalty", "description"}


def test_missing_evidence_notes_are_field_names_only(
    client: TestClient,
    pending_case: PersistedCase,
) -> None:
    notes = client.get(f"{CASES_URL}/{pending_case.review_case_id}").json()[
        "missing_evidence_notes"
    ]

    assert notes == list(pending_case.case.missing_evidence_notes)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


def test_events_for_a_case_with_history() -> None:
    case, state, _ = build_case("e-1", "e-2", decision=HumanReviewDecision.MATCH, reviewer_id="r")
    persisted = persist(case, version=2)
    created = ReviewEvent.case_created(
        case.review_case_id, occurred_at_utc=NOW, schema_version="1.0.0", event_id=1
    )
    resolved = resolution_event(state)
    client = api_client(
        FakeReviewCaseRepository(
            cases=(persisted,), events={case.review_case_id: (created, resolved)}
        )
    )

    body = client.get(f"{CASES_URL}/{case.review_case_id}/events").json()

    assert [event["event_type"] for event in body] == ["CASE_CREATED", "MATCH"]
    assert [event["event_id"] for event in body] == [1, 2]


def test_event_field_set_is_pinned() -> None:
    case, state, _ = build_case("f-1", "f-2", decision=HumanReviewDecision.MATCH, reviewer_id="r")
    client = api_client(
        FakeReviewCaseRepository(
            cases=(persist(case, version=2),),
            events={case.review_case_id: (resolution_event(state),)},
        )
    )

    for event in client.get(f"{CASES_URL}/{case.review_case_id}/events").json():
        assert set(event) == EVENT_FIELDS


def test_a_human_resolution_event_is_marked_as_one() -> None:
    case, state, _ = build_case(
        "g-1", "g-2", decision=HumanReviewDecision.NO_MATCH, reviewer_id="r"
    )
    client = api_client(
        FakeReviewCaseRepository(
            cases=(persist(case, version=2),),
            events={case.review_case_id: (resolution_event(state),)},
        )
    )

    event = client.get(f"{CASES_URL}/{case.review_case_id}/events").json()[0]

    assert event["is_resolution"] is True
    assert event["event_type"] == "NO_MATCH"
    assert event["resolution_sequence"] == 1
    assert event["reviewer_id"] == "r"
    assert event["suggestion_id"] is None


def test_a_semantic_event_is_not_a_resolution(pending_case: PersistedCase) -> None:
    """An advisory observation appears in history and decides nothing."""
    semantic = ReviewEvent.semantic_suggestion_recorded(
        pending_case.review_case_id,
        suggestion_id="LS-abc123",
        occurred_at_utc=LATER,
        schema_version="1.0.0",
        event_id=2,
    )
    client = api_client(
        FakeReviewCaseRepository(
            cases=(pending_case,), events={pending_case.review_case_id: (semantic,)}
        )
    )

    event = client.get(f"{CASES_URL}/{pending_case.review_case_id}/events").json()[0]

    assert event["is_resolution"] is False
    assert event["event_type"] == "SEMANTIC_SUGGESTION_RECORDED"
    assert event["resolution_sequence"] is None
    assert event["reviewer_id"] is None
    assert event["suggestion_id"] == "LS-abc123"


def test_events_omit_persistence_and_audit_payload() -> None:
    case, state, _ = build_case("h-1", "h-2", decision=HumanReviewDecision.MATCH, reviewer_id="r")
    client = api_client(
        FakeReviewCaseRepository(
            cases=(persist(case, version=2),),
            events={case.review_case_id: (resolution_event(state),)},
        )
    )

    body = client.get(f"{CASES_URL}/{case.review_case_id}/events").text

    assert "schema_version" not in body
    assert "audit_entry_payload" not in body
    assert "downstream_action" not in body


def test_events_for_a_case_with_no_history(client: TestClient, pending_case: PersistedCase) -> None:
    response = client.get(f"{CASES_URL}/{pending_case.review_case_id}/events")

    assert response.status_code == 200
    assert response.json() == []


def test_events_for_an_unknown_case_are_a_404(client: TestClient) -> None:
    """The repository returns () for both cases; only get_case can tell them apart."""
    response = client.get(f"{CASES_URL}/RC-doesnotexist0000/events")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVIEW_CASE_NOT_FOUND"


# --------------------------------------------------------------------------
# Semantic suggestions
# --------------------------------------------------------------------------


def suggestion_client(persisted: PersistedCase, *suggestions: object) -> TestClient:
    return api_client(
        FakeReviewCaseRepository(
            cases=(persisted,),
            suggestions={persisted.review_case_id: suggestions},  # type: ignore[dict-item]
        )
    )


def test_suggestions_for_a_case_with_none(client: TestClient, pending_case: PersistedCase) -> None:
    response = client.get(f"{CASES_URL}/{pending_case.review_case_id}/semantic-suggestions")

    assert response.status_code == 200
    assert response.json() == []


def test_suggestions_for_an_unknown_case_are_a_404(client: TestClient) -> None:
    response = client.get(f"{CASES_URL}/RC-doesnotexist0000/semantic-suggestions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVIEW_CASE_NOT_FOUND"


def test_one_suggestion_is_projected() -> None:
    case, _, resolution = build_case("i-1", "i-2")
    persisted = persist(case)
    suggestion = make_suggestion(case, records_by_id(resolution))

    body = (
        suggestion_client(persisted, suggestion)
        .get(f"{CASES_URL}/{case.review_case_id}/semantic-suggestions")
        .json()
    )

    assert len(body) == 1
    assert set(body[0]) == SUGGESTION_FIELDS
    assert body[0]["suggestion_id"] == suggestion.suggestion_id
    assert body[0]["suggestion"] == "SUGGEST_MATCH"
    assert body[0]["reason_codes"] == list(suggestion.reason_codes)
    assert body[0]["live"] is False
    assert body[0]["failure_code"] is None


def test_every_suggestion_is_marked_advisory() -> None:
    case, _, resolution = build_case("j-1", "j-2")
    suggestion = make_suggestion(case, records_by_id(resolution))

    body = (
        suggestion_client(persist(case), suggestion)
        .get(f"{CASES_URL}/{case.review_case_id}/semantic-suggestions")
        .json()
    )

    assert body[0]["advisory"] is True


def test_multiple_suggestions_preserve_observation_order() -> None:
    case, _, resolution = build_case("k-1", "k-2")
    first = make_suggestion(case, records_by_id(resolution))
    second = as_provider_failure(first)

    body = (
        suggestion_client(persist(case), first, second)
        .get(f"{CASES_URL}/{case.review_case_id}/semantic-suggestions")
        .json()
    )

    assert [item["suggestion_id"] for item in body] == [first.suggestion_id, second.suggestion_id]


def test_a_provider_failure_is_projected_as_advisory() -> None:
    """PROVIDER_FAILURE is a Sprint 09 advisory value, not an API error."""
    case, _, resolution = build_case("l-1", "l-2")
    failure = as_provider_failure(make_suggestion(case, records_by_id(resolution)))

    response = suggestion_client(persist(case), failure).get(
        f"{CASES_URL}/{case.review_case_id}/semantic-suggestions"
    )

    assert response.status_code == 200
    assert response.json()[0]["suggestion"] == "PROVIDER_FAILURE"
    assert response.json()[0]["failure_code"] == "PROVIDER_TIMEOUT"
    assert response.json()[0]["advisory"] is True


def test_the_explanation_never_reaches_the_client() -> None:
    """Non-vacuous: the in-memory suggestion really does carry explanation text.

    A suggestion reloaded from storage has an empty explanation because Sprint 09
    never persists it. This one comes straight from the provider, so the field is
    populated -- which is the only way to prove the API drops it rather than
    merely inheriting an empty value.
    """
    case, _, resolution = build_case("m-1", "m-2")
    suggestion = make_suggestion(case, records_by_id(resolution))
    assert suggestion.explanation, "Fixture must carry explanation text to be meaningful."

    response = suggestion_client(persist(case), suggestion).get(
        f"{CASES_URL}/{case.review_case_id}/semantic-suggestions"
    )

    assert suggestion.explanation not in response.text
    assert "explanation" not in response.text
    assert "explanation" not in response.json()[0]


def test_provider_telemetry_is_not_published() -> None:
    case, _, resolution = build_case("n-1", "n-2")
    suggestion = make_suggestion(case, records_by_id(resolution))

    body = (
        suggestion_client(persist(case), suggestion)
        .get(f"{CASES_URL}/{case.review_case_id}/semantic-suggestions")
        .text
    )

    for token in (
        "estimated_cost_usd",
        "actual_provider_cost_usd",
        "hypothetical_model_cost_usd",
        "input_token_count",
        "output_token_count",
        "cached_input_token_count",
        "reasoning_token_count",
        "latency_ms",
        "provider_request_id",
        "request_fingerprint",
        "request_id",
        "prompt_version",
        "prompt_template_hash",
        "request_schema_version",
        "response_schema_version",
        "attempt_count",
        "cost_mode",
        "usage_available",
        "pricing_version",
        "returned_model",
        "model_snapshot",
        "result_source",
    ):
        assert token not in body, f"{token} leaked into a semantic suggestion response"


# --------------------------------------------------------------------------
# Side-effect freedom
# --------------------------------------------------------------------------


def test_no_read_endpoint_touches_an_authority_method(
    client: TestClient,
    queue: tuple[PersistedCase, ...],
) -> None:
    """The fake raises on every write; exercising all four routes must not trip it."""
    case_id = queue[0].review_case_id

    for url in (
        CASES_URL,
        f"{CASES_URL}?status=PENDING",
        f"{CASES_URL}/{case_id}",
        f"{CASES_URL}/{case_id}/events",
        f"{CASES_URL}/{case_id}/semantic-suggestions",
    ):
        assert client.get(url).status_code == 200


def test_the_fake_really_would_detonate(repository: FakeReviewCaseRepository) -> None:
    """Guards the test above from passing because the fake is inert."""
    with pytest.raises(AssertionError):
        repository.load_workflow_bundle()
    with pytest.raises(AssertionError):
        repository.record_semantic_suggestion(object(), now_utc=NOW)  # type: ignore[arg-type]


def test_reads_do_not_change_what_the_repository_holds(
    client: TestClient,
    repository: FakeReviewCaseRepository,
    queue: tuple[PersistedCase, ...],
) -> None:
    before = [(c.review_case_id, c.status, c.version, c.updated_at_utc) for c in queue]

    client.get(CASES_URL)
    client.get(f"{CASES_URL}/{queue[0].review_case_id}")

    after = [
        (c.review_case_id, c.status, c.version, c.updated_at_utc)
        for c in repository.list_cases(status=None)
    ]
    assert after == before


# --------------------------------------------------------------------------
# Error safety on the read paths
# --------------------------------------------------------------------------


def test_a_not_found_message_cannot_leak_its_own_text(pending_case: PersistedCase) -> None:
    """``ReviewCaseNotFoundError`` embeds the requested id; the handler ignores it."""
    client = api_client(FakeReviewCaseRepository(cases=(pending_case,)))

    response = client.get(f"{CASES_URL}/RC-SENTINEL-MISSING-9182")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "No review case exists with that identifier."
    assert "RC-SENTINEL-MISSING-9182" not in response.text
