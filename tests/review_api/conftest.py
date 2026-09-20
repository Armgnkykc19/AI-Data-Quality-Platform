"""Fixtures for the API tests.

The probe application exists because Phase A shipped no route that accepts input
and none that fails. Both behaviours still have to be tested: the error envelope
is a contract, and a contract nothing exercises is a guess. The probes are
registered on a test-built application, never in production code -- an endpoint
whose purpose is to raise would be a permanent liability in a service with no
authentication.

The review-case fixtures build **real Sprint 08 objects**. Cases come from
``generate_review_cases`` and resolved cases from ``ReviewWorkflow.resolve_case``;
suggestions come from the offline Sprint 09 provider. Nothing here is a
hand-built lookalike, because a projection test only means something if what it
projects is what production actually produces.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, field_validator

from entity_resolution.config import load_entity_resolution_config
from entity_resolution.models import EntityRecord, ResolutionResult
from human_review.cases import generate_review_cases
from human_review.models import (
    HumanReviewDecision,
    ReviewAuditEntry,
    ReviewCase,
    ReviewWorkflowState,
)
from human_review.workflow import ReviewWorkflow
from review_api import create_app
from review_application import PersistedCase, ReviewEvent, ReviewResolutionResult
from tests.human_review.conftest import make_review_resolution, match_authorization_kwargs
from tests.review_api.auth_fixtures import AuthFixture, build_auth_fixture

# Values that could not plausibly appear in this API's own vocabulary, so a test
# can assert they are absent from a response and mean it.
SENTINEL_INPUT = "SENTINEL-CLIENT-VALUE-2f8c41"
SENSITIVE_VALIDATOR_SENTINEL = "SENSITIVE-CUSTOM-VALIDATOR-VALUE-9f7a31"

# The prose a custom validator puts in its exception. Pydantic lifts this into
# the error's "msg", so it is the second thing a sanitizer must not forward.
CUSTOM_VALIDATOR_PREFIX = "Unknown reviewer"

NOW = "2026-09-14T08:00:00Z"
LATER = "2026-09-14T09:00:00Z"


# --------------------------------------------------------------------------
# Probe application (error-handler tests only)
# --------------------------------------------------------------------------


class ProbeBody(BaseModel):
    """Stands in for a Phase C request model, with the same strictness."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int


class ProbeCustomValidatorBody(BaseModel):
    """A model whose validator leaks, the way a real one eventually will.

    Built-in Pydantic failures produce safe wording, which makes a sanitizer
    that keeps ``msg`` look correct. A custom validator is where that breaks:
    the exception text is author-written, it routinely interpolates the value
    that failed, and Pydantic copies it verbatim into the error. This model
    exists so the sanitizer is tested against that case rather than only
    against the framework's own well-behaved messages.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str

    @field_validator("reviewer_id")
    @classmethod
    def reject_unknown_reviewer(cls, value: str) -> str:
        raise ValueError(f"{CUSTOM_VALIDATOR_PREFIX} {value}; not in the roster")


@pytest.fixture
def app() -> FastAPI:
    """A plain application: routes, error handlers, and no storage."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def probe_app() -> FastAPI:
    """An application with test-only routes that the error handlers see.

    ``/probe/boom`` raises an exception carrying text that must never surface.
    ``/probe/validate`` accepts a strict body, so a malformed request produces
    a real ``RequestValidationError`` rather than a simulated one.
    ``/probe/custom-validate`` fails inside an author-written validator, which
    is the case built-in Pydantic errors cannot exercise.
    """
    probe = create_app()

    @probe.get("/probe/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError(
            f"database is locked: storage/review_queue.db; "
            f"SELECT * FROM review_cases WHERE version = {SENTINEL_INPUT}"
        )

    @probe.post("/probe/validate")
    async def validate(body: ProbeBody) -> dict[str, int]:
        return {"expected_version": body.expected_version}

    @probe.post("/probe/custom-validate")
    async def custom_validate(body: ProbeCustomValidatorBody) -> dict[str, str]:
        # Unreachable: the validator always raises.
        return {"reviewer_id": body.reviewer_id}  # pragma: no cover

    return probe


@pytest.fixture
def probe_client(probe_app: FastAPI) -> Iterator[TestClient]:
    """Client that returns the 500 response instead of re-raising.

    Starlette's server-error middleware calls the catch-all handler to build the
    response and then re-raises so the exception reaches the server's log. That
    re-raise is what ``raise_server_exceptions=False`` suppresses; the response
    a real client would receive is unchanged either way.
    """
    with TestClient(probe_app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Real Sprint 08 review cases
# --------------------------------------------------------------------------


def build_review_state(left_id: str, right_id: str) -> tuple[ResolutionResult, ReviewWorkflowState]:
    """A genuine REVIEW case for one record pair, via production generation."""
    resolution = make_review_resolution(left_id, right_id)
    state = generate_review_cases(resolution, config=load_entity_resolution_config())
    assert state.cases, "Fixture resolution must produce at least one REVIEW case."
    return resolution, state


def build_case(
    left_id: str,
    right_id: str,
    *,
    decision: HumanReviewDecision | None = None,
    reviewer_id: str | None = None,
) -> tuple[ReviewCase, ReviewWorkflowState, ResolutionResult]:
    """A case, optionally resolved by the real Sprint 08 workflow.

    Resolved statuses are never assigned by hand. ``ReviewWorkflow.resolve_case``
    is the only thing allowed to produce one, so a projection test cannot pass
    against a status the domain would have refused to set.
    """
    resolution, state = build_review_state(left_id, right_id)
    case_id = state.cases[0].review_case_id
    if decision is None:
        return state.cases[0], state, resolution

    kwargs = (
        match_authorization_kwargs(resolution, load_entity_resolution_config())
        if decision is HumanReviewDecision.MATCH
        else {}
    )
    resolved_state = ReviewWorkflow(state).resolve_case(
        case_id, decision=decision, reviewer_id=reviewer_id, **kwargs
    )
    resolved = resolved_state.case_by_id(case_id)
    assert resolved is not None
    return resolved, resolved_state, resolution


def persist(case: ReviewCase, *, version: int = 1) -> PersistedCase:
    """Wrap a domain case in the persistence metadata the API projects."""
    return PersistedCase(
        case=case,
        version=version,
        created_at_utc=NOW,
        updated_at_utc=NOW if version == 1 else LATER,
    )


def records_by_id(resolution: ResolutionResult) -> dict[str, EntityRecord]:
    return {record.record_id: record for record in resolution.records}


def last_audit_entry(state: ReviewWorkflowState) -> ReviewAuditEntry:
    assert state.audit_trail, "Resolving a case must append an audit entry."
    return state.audit_trail[-1]


@pytest.fixture
def pending_case() -> PersistedCase:
    case, _, _ = build_case("a-1", "a-2")
    return persist(case)


@pytest.fixture
def resolved_match_case() -> PersistedCase:
    case, _, _ = build_case("b-1", "b-2", decision=HumanReviewDecision.MATCH, reviewer_id="rev-1")
    return persist(case, version=2)


@pytest.fixture
def resolved_no_match_case() -> PersistedCase:
    case, _, _ = build_case(
        "c-1", "c-2", decision=HumanReviewDecision.NO_MATCH, reviewer_id="rev-2"
    )
    return persist(case, version=2)


@pytest.fixture
def deferred_case() -> PersistedCase:
    case, _, _ = build_case("d-1", "d-2", decision=HumanReviewDecision.DEFER, reviewer_id="rev-3")
    return persist(case, version=2)


@pytest.fixture
def queue(
    pending_case: PersistedCase,
    resolved_match_case: PersistedCase,
    resolved_no_match_case: PersistedCase,
    deferred_case: PersistedCase,
) -> tuple[PersistedCase, ...]:
    """One case per status, in a fixed order the list tests assert is preserved."""
    return (pending_case, resolved_match_case, resolved_no_match_case, deferred_case)


def resolution_event(state: ReviewWorkflowState, *, event_id: int = 2) -> ReviewEvent:
    """A history event built the only supported way: from the domain audit entry."""
    return ReviewEvent.from_audit_entry(
        last_audit_entry(state), occurred_at_utc=LATER, schema_version="1.0.0", event_id=event_id
    )


def api_client(repository: object) -> TestClient:
    """A client over an app wired to the given repository and nothing else."""
    return TestClient(create_app(repository=repository))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Phase C: resolution
# --------------------------------------------------------------------------


def resolution_result(
    left_id: str,
    right_id: str,
    *,
    decision: HumanReviewDecision = HumanReviewDecision.MATCH,
    reviewer_id: str | None = "rev-1",
) -> ReviewResolutionResult:
    """A real ``ReviewResolutionResult``, assembled from a real domain resolution.

    The workflow produces the case and the audit entry; only the persistence
    metadata is supplied here, standing in for what storage would have stamped.
    Nothing about the decision itself is hand-built.
    """
    case, state, _ = build_case(left_id, right_id, decision=decision, reviewer_id=reviewer_id)
    entry = last_audit_entry(state)
    return ReviewResolutionResult(
        persisted_case=persist(case, version=2),
        audit_entry=entry,
        event=ReviewEvent.from_audit_entry(entry, occurred_at_utc=LATER),
        workflow_state=state,
    )


def service_client(service: object) -> TestClient:
    """A client over an app wired to the given service and no repository."""
    return TestClient(create_app(service=service), raise_server_exceptions=False)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Sprint 13 authentication
# --------------------------------------------------------------------------
#
# These two build something the rest of this file deliberately does not: a real
# SQLite database, real Argon2id hashing and a real session service. The
# machinery lives in ``auth_fixtures`` -- it is long enough to want its own
# docstring -- and only the fixture wrappers are here, because pytest discovers
# fixtures in conftest files.


@pytest.fixture
def auth(tmp_path: Path) -> Iterator[AuthFixture]:
    """A fully wired authenticated API over a temporary database."""
    yield from build_auth_fixture(tmp_path)


@pytest.fixture
def signed_in(auth: AuthFixture) -> AuthFixture:
    """The same, with a provisioned user already logged in."""
    auth.create_user()
    auth.login_as()
    return auth
