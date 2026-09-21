"""Who may reach which queue, proven against the real composition.

Everything here runs over real SQLite, real Argon2id credentials, real sessions
carried in a real cookie, and the real ``TenantAuthorizationService`` reading
the real membership table. No directory is faked and no principal is injected,
because every property in this file is a property of that composition: a fake
membership read would make the answer the test's own invention.

The arrangement is fixed for the whole module and is the smallest one that can
express every case worth checking:

* organization **A** owns queues **A1** and **A2**, so a single application
  serves more than one queue and a URL has something to choose between;
* organization **B** owns queue **B1**, so "another tenant" is a real tenant
  with real data rather than an absence;
* **A1** and **B1** are registered from the *same* generated workflow, so they
  hold a review case with the same deterministic id -- the collision that queue
  scoping exists to keep apart;
* **A2** holds three cases, so a total or a page that ever spanned queues would
  be visibly wrong rather than coincidentally right.

Three people: a REVIEWER and a VIEWER in A, and an outsider with a perfectly
good account and no membership anywhere.

No password, raw token or token digest is printed or asserted on. Every
database is under ``tmp_path``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from identity.models import MembershipRole, Organization, User
from review_application.queues import ReviewQueue
from tests.human_review.conftest import (
    make_review_resolution,
    make_triangle_review_resolution,
)
from tests.review_api.auth_fixtures import (
    ALLOWED_ORIGIN,
    FOREIGN_ORIGIN,
    PASSWORD,
    AuthFixture,
)
from tests.review_persistence.semantic_fixtures import make_suggestion

REVIEWER_EMAIL = "reviewer@example.test"
VIEWER_EMAIL = "viewer@example.test"
OUTSIDER_EMAIL = "outsider@example.test"

UNKNOWN_ORGANIZATION = "ORG-does-not-exist"
UNKNOWN_QUEUE = "RQ-does-not-exist"


@dataclass(frozen=True)
class Tenancy:
    """The fixed arrangement described in the module docstring."""

    auth: AuthFixture
    organization_a: Organization
    organization_b: Organization
    queue_a1: ReviewQueue
    queue_a2: ReviewQueue
    queue_b1: ReviewQueue
    reviewer: User
    viewer: User
    outsider: User
    shared_case_id: str
    a2_case_ids: tuple[str, ...]

    # -- addressing ---------------------------------------------------------

    def url(self, organization_id: str, review_queue_id: str, suffix: str = "") -> str:
        return (
            f"/api/v1/organizations/{organization_id}"
            f"/review-queues/{review_queue_id}/review-cases{suffix}"
        )

    def a1(self, suffix: str = "") -> str:
        return self.url(self.organization_a.organization_id, self.queue_a1.review_queue_id, suffix)

    def a2(self, suffix: str = "") -> str:
        return self.url(self.organization_a.organization_id, self.queue_a2.review_queue_id, suffix)

    def b1(self, suffix: str = "") -> str:
        return self.url(self.organization_b.organization_id, self.queue_b1.review_queue_id, suffix)

    # -- driving ------------------------------------------------------------

    def sign_in(self, email: str) -> str:
        token = self.auth.login_as(email=email, password=PASSWORD)
        self.auth.counters.reset()
        return token

    def get(self, url: str):
        return self.auth.client.get(url)

    def resolve(self, url: str, *, version: int = 1, origin: str | None = ALLOWED_ORIGIN):
        headers = {} if origin is None else {"Origin": origin}
        return self.auth.client.post(
            url,
            json={"decision": "NO_MATCH", "expected_version": version},
            headers=headers,
        )


@pytest.fixture
def tenancy(auth: AuthFixture) -> Tenancy:
    organization_a = auth.create_organization("org-a")
    organization_b = auth.create_organization("org-b")
    queue_a1 = auth.create_queue(organization_a, "a-one")
    queue_a2 = auth.create_queue(organization_a, "a-two")
    queue_b1 = auth.create_queue(organization_b, "b-one")

    # The same workflow into A1 and B1: identical case ids in two tenants.
    shared = make_review_resolution("a-1", "a-2")
    state_a1 = auth.register_workflow(queue_a1, shared)
    auth.register_workflow(queue_b1, shared)
    state_a2 = auth.register_workflow(
        queue_a2, make_triangle_review_resolution(("rec-a", "rec-b", "rec-c"))
    )

    reviewer = auth.create_user(email=REVIEWER_EMAIL, display_name="Reviewer")
    viewer = auth.create_user(email=VIEWER_EMAIL, display_name="Viewer")
    outsider = auth.create_user(email=OUTSIDER_EMAIL, display_name="Outsider")
    auth.grant(reviewer, organization_a, MembershipRole.REVIEWER)
    auth.grant(viewer, organization_a, MembershipRole.VIEWER)

    return Tenancy(
        auth=auth,
        organization_a=organization_a,
        organization_b=organization_b,
        queue_a1=queue_a1,
        queue_a2=queue_a2,
        queue_b1=queue_b1,
        reviewer=reviewer,
        viewer=viewer,
        outsider=outsider,
        shared_case_id=state_a1.cases[0].review_case_id,
        a2_case_ids=tuple(case.review_case_id for case in state_a2.cases),
    )


def read_paths(tenancy: Tenancy) -> list[str]:
    """The four read operations, addressed at queue A1."""
    case = tenancy.shared_case_id
    return [
        tenancy.a1(),
        tenancy.a1(f"/{case}"),
        tenancy.a1(f"/{case}/events"),
        tenancy.a1(f"/{case}/semantic-suggestions"),
    ]


# --------------------------------------------------------------------------
# The authorization matrix
# --------------------------------------------------------------------------


def test_a_reviewer_may_read_every_projection(tenancy: Tenancy) -> None:
    tenancy.sign_in(REVIEWER_EMAIL)

    for url in read_paths(tenancy):
        assert tenancy.get(url).status_code == 200, url


def test_a_viewer_may_read_every_projection(tenancy: Tenancy) -> None:
    """READ_REVIEW_QUEUE is the whole of a VIEWER's capability set, and it is real."""
    tenancy.sign_in(VIEWER_EMAIL)

    for url in read_paths(tenancy):
        assert tenancy.get(url).status_code == 200, url


def test_a_non_member_gets_404_from_every_projection(tenancy: Tenancy) -> None:
    """Not 403. A non-member must not learn that the queue exists."""
    tenancy.sign_in(OUTSIDER_EMAIL)

    for url in read_paths(tenancy):
        response = tenancy.get(url)
        assert response.status_code == 404, url
        assert response.json()["error"]["code"] == "NOT_FOUND"


def test_an_unauthenticated_caller_gets_401_from_every_projection(tenancy: Tenancy) -> None:
    tenancy.auth.set_cookie(None)

    for url in read_paths(tenancy):
        response = tenancy.get(url)
        assert response.status_code == 401, url
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_a_viewer_is_refused_the_resolution_endpoint(tenancy: Tenancy) -> None:
    """403 and ``FORBIDDEN``: a member whose role does not carry the capability."""
    tenancy.sign_in(VIEWER_EMAIL)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_a_viewers_refusal_reaches_no_application_code(tenancy: Tenancy) -> None:
    """The refusal happens above the service, so nothing is evaluated or written."""
    tenancy.sign_in(VIEWER_EMAIL)
    before = tenancy.auth.repository_for(tenancy.queue_a1).get_case(tenancy.shared_case_id)

    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    after = tenancy.auth.repository_for(tenancy.queue_a1).get_case(tenancy.shared_case_id)
    assert (after.status, after.version) == (before.status, before.version)
    assert tenancy.auth.repository_for(tenancy.queue_a1).list_events(tenancy.shared_case_id) == ()


def test_a_non_member_is_refused_the_resolution_endpoint_with_404(tenancy: Tenancy) -> None:
    tenancy.sign_in(OUTSIDER_EMAIL)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_an_unauthenticated_resolution_is_401(tenancy: Tenancy) -> None:
    tenancy.auth.set_cookie(None)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert response.status_code == 401


def test_a_reviewer_reaches_the_application_layer(tenancy: Tenancy) -> None:
    """ "Domain" in the matrix means exactly this: the decision is applied."""
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "NO_MATCH"


# --------------------------------------------------------------------------
# The wrong-scope matrix
# --------------------------------------------------------------------------


def wrong_scopes(tenancy: Tenancy) -> dict[str, str]:
    """Every way a URL can name a scope this reviewer may not reach."""
    return {
        "unknown organization": tenancy.url(UNKNOWN_ORGANIZATION, UNKNOWN_QUEUE),
        "known organization, unknown queue": tenancy.url(
            tenancy.organization_a.organization_id, UNKNOWN_QUEUE
        ),
        "own organization paired with a foreign queue": tenancy.url(
            tenancy.organization_a.organization_id, tenancy.queue_b1.review_queue_id
        ),
        "foreign organization paired with an own queue": tenancy.url(
            tenancy.organization_b.organization_id, tenancy.queue_a1.review_queue_id
        ),
        "another organization entirely": tenancy.b1(),
    }


def test_every_wrong_scope_is_a_404_for_a_reviewer(tenancy: Tenancy) -> None:
    tenancy.sign_in(REVIEWER_EMAIL)

    for label, url in wrong_scopes(tenancy).items():
        response = tenancy.get(url)
        assert response.status_code == 404, label
        assert response.json()["error"]["code"] == "NOT_FOUND", label


def test_every_wrong_scope_is_a_404_for_a_resolution_too(tenancy: Tenancy) -> None:
    """The write path uses the same proof, so it cannot be looser than the reads."""
    tenancy.sign_in(REVIEWER_EMAIL)

    for label, url in wrong_scopes(tenancy).items():
        response = tenancy.resolve(f"{url}/{tenancy.shared_case_id}/resolve")
        assert response.status_code == 404, label


def test_a_mismatched_pairing_is_never_silently_corrected(tenancy: Tenancy) -> None:
    """Naming A with B's queue must not serve B's queue, nor A's.

    This is the substitution attack the ownership check exists for. The
    response must not be B1's data under an A-shaped URL, and it must not be
    quietly redirected to A1 either.
    """
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.get(
        tenancy.url(tenancy.organization_a.organization_id, tenancy.queue_b1.review_queue_id)
    )

    assert response.status_code == 404
    assert "items" not in response.text


def test_a_member_of_both_organizations_reaches_each_through_its_own_url(
    tenancy: Tenancy,
) -> None:
    """Membership in two tenants does not merge them.

    The same user, the same session, the same case id -- and two separate
    records, each reachable only through the URL that names its own queue.
    """
    tenancy.auth.grant(tenancy.reviewer, tenancy.organization_b, MembershipRole.REVIEWER)
    tenancy.sign_in(REVIEWER_EMAIL)

    in_a = tenancy.get(tenancy.a1(f"/{tenancy.shared_case_id}"))
    in_b = tenancy.get(tenancy.b1(f"/{tenancy.shared_case_id}"))

    assert in_a.status_code == 200
    assert in_b.status_code == 200
    assert in_a.json()["review_case_id"] == in_b.json()["review_case_id"]
    # And still the mismatched pairing is refused.
    assert (
        tenancy.get(
            tenancy.url(tenancy.organization_a.organization_id, tenancy.queue_b1.review_queue_id)
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------
# Existence leakage
# --------------------------------------------------------------------------


def test_a_non_members_404_is_byte_identical_to_an_invented_tenants(
    tenancy: Tenancy,
) -> None:
    """The core of the leakage policy, stated as an equality.

    Organization A exists and holds data; ``ORG-does-not-exist`` does not exist
    at all. To someone with no membership the two must be the same answer, or
    the difference is an oracle for enumerating tenants.
    """
    tenancy.sign_in(OUTSIDER_EMAIL)

    real = tenancy.get(tenancy.a1())
    invented = tenancy.get(tenancy.url(UNKNOWN_ORGANIZATION, UNKNOWN_QUEUE))

    assert real.status_code == invented.status_code == 404
    assert real.json() == invented.json()


def test_no_refusal_names_another_tenant(tenancy: Tenancy) -> None:
    """Not the other organization, not its queue, not its slug, not its case."""
    tenancy.sign_in(REVIEWER_EMAIL)

    for url in wrong_scopes(tenancy).values():
        body = tenancy.get(url).text
        for secret in (
            tenancy.organization_b.organization_id,
            tenancy.organization_b.slug,
            tenancy.queue_b1.review_queue_id,
            tenancy.queue_b1.name,
            tenancy.shared_case_id,
        ):
            assert secret not in body, f"{secret} leaked from {url}"


def test_a_refusal_carries_no_details(tenancy: Tenancy) -> None:
    """One static envelope. ``details`` stays null rather than echoing the scope."""
    tenancy.sign_in(OUTSIDER_EMAIL)

    payload = tenancy.get(tenancy.a1()).json()

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["details"] is None
    assert payload["error"]["message"] == "The requested resource was not found."


def test_a_403_says_nothing_about_the_role_it_wanted(tenancy: Tenancy) -> None:
    """Naming the missing capability would hand an attacker an escalation target."""
    tenancy.sign_in(VIEWER_EMAIL)

    body = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve")).text

    for token in ("REVIEWER", "VIEWER", "RESOLVE_REVIEW_CASE", "role", "membership"):
        assert token not in body


# --------------------------------------------------------------------------
# Membership freshness: the session carries identity and nothing else
# --------------------------------------------------------------------------


def test_a_promotion_takes_effect_without_a_new_login(tenancy: Tenancy) -> None:
    """VIEWER to REVIEWER, mid-session, with the same cookie throughout.

    If a role were captured at login this would still be a 403 -- for up to
    eight hours. Authorization reads the membership row on every request, so it
    is not.
    """
    tenancy.sign_in(VIEWER_EMAIL)
    url = tenancy.a1(f"/{tenancy.shared_case_id}/resolve")
    assert tenancy.resolve(url).status_code == 403

    tenancy.auth.set_role(tenancy.viewer, tenancy.organization_a, MembershipRole.REVIEWER)

    assert tenancy.resolve(url).status_code == 200


def test_a_demotion_takes_effect_without_a_new_login(tenancy: Tenancy) -> None:
    """The direction that actually matters for revocation.

    A capability removed by an operator must stop working immediately, not
    whenever the person happens to close their browser.
    """
    tenancy.sign_in(REVIEWER_EMAIL)
    case_id = tenancy.a2_case_ids[0]
    url = tenancy.a2(f"/{case_id}/resolve")

    tenancy.auth.set_role(tenancy.reviewer, tenancy.organization_a, MembershipRole.REVIEWER)
    assert tenancy.get(tenancy.a2(f"/{case_id}")).status_code == 200

    tenancy.auth.set_role(tenancy.reviewer, tenancy.organization_a, MembershipRole.VIEWER)

    assert tenancy.resolve(url).status_code == 403
    # Reading survives, because READ_REVIEW_QUEUE is still theirs.
    assert tenancy.get(tenancy.a2(f"/{case_id}")).status_code == 200


def test_a_new_membership_takes_effect_without_a_new_login(tenancy: Tenancy) -> None:
    """The outsider signs in first and is granted access afterwards."""
    tenancy.sign_in(OUTSIDER_EMAIL)
    assert tenancy.get(tenancy.a1()).status_code == 404

    tenancy.auth.grant(tenancy.outsider, tenancy.organization_a, MembershipRole.VIEWER)

    assert tenancy.get(tenancy.a1()).status_code == 200


def test_the_session_response_carries_no_tenant_or_role(tenancy: Tenancy) -> None:
    """Identity only. A published role is a role a client would cache."""
    tenancy.sign_in(REVIEWER_EMAIL)

    body = tenancy.auth.get_session().text

    for token in (
        tenancy.organization_a.organization_id,
        tenancy.organization_a.slug,
        tenancy.queue_a1.review_queue_id,
        "REVIEWER",
        "VIEWER",
        "role",
        "membership",
        "organization",
        "queue",
    ):
        assert token not in body


def test_the_stored_session_row_carries_no_tenant_or_role(tenancy: Tenancy) -> None:
    """Asserted against the table, because the response could simply hide a column."""
    token = tenancy.sign_in(REVIEWER_EMAIL)
    stored = tenancy.auth.stored_session(token)

    assert stored is not None
    columns = {
        description[0]
        for description in tenancy.auth.database.connect()
        .execute("SELECT * FROM user_sessions LIMIT 0")
        .description
    }
    for forbidden in ("organization_id", "review_queue_id", "role", "membership_id", "capability"):
        assert forbidden not in columns


# --------------------------------------------------------------------------
# One request, one session touch
# --------------------------------------------------------------------------


def test_an_authorized_read_touches_the_session_exactly_once(tenancy: Tenancy) -> None:
    """The guarantee the whole dependency chain is arranged to preserve.

    The scope dependency and the scoped repository both need the principal, and
    the route needs the repository. FastAPI caches a dependency per request, so
    that is one resolution and one renewal -- not three.
    """
    tenancy.sign_in(REVIEWER_EMAIL)

    tenancy.get(tenancy.a1())

    assert tenancy.auth.counters.touch_calls == 1
    assert tenancy.auth.counters.resolve_calls == 1


def test_an_authorized_resolution_touches_the_session_exactly_once(
    tenancy: Tenancy,
) -> None:
    """The deepest chain in the API: origin, principal, scope, service, domain."""
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert response.status_code == 200
    assert tenancy.auth.counters.touch_calls == 1
    assert tenancy.auth.counters.resolve_calls == 1


def test_every_read_projection_touches_exactly_once(tenancy: Tenancy) -> None:
    """One per request, across four routes -- four requests, four touches."""
    tenancy.sign_in(REVIEWER_EMAIL)

    for url in read_paths(tenancy):
        tenancy.get(url)

    assert tenancy.auth.counters.touch_calls == len(read_paths(tenancy))


def test_a_refused_scope_still_touches_only_once(tenancy: Tenancy) -> None:
    """Authorization runs after the renewal and does not repeat it."""
    tenancy.sign_in(OUTSIDER_EMAIL)

    assert tenancy.get(tenancy.a1()).status_code == 404

    assert tenancy.auth.counters.touch_calls == 1


def test_a_rejected_origin_touches_nothing(tenancy: Tenancy) -> None:
    """The origin check runs first, so a forged request costs no session work."""
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.resolve(
        tenancy.a1(f"/{tenancy.shared_case_id}/resolve"), origin=FOREIGN_ORIGIN
    )

    assert response.status_code == 403
    assert tenancy.auth.counters.touch_calls == 0
    assert tenancy.auth.counters.resolve_calls == 0


# --------------------------------------------------------------------------
# Origin protection on the one state-changing route
# --------------------------------------------------------------------------


@pytest.mark.parametrize("origin", [None, FOREIGN_ORIGIN, "http://127.0.0.1:5174", "null"])
def test_only_a_trusted_origin_may_resolve(tenancy: Tenancy, origin: str | None) -> None:
    """Missing, foreign, wrong-port and the literal ``null`` are all refused."""
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"), origin=origin)

    assert response.status_code == 403
    stored = tenancy.auth.repository_for(tenancy.queue_a1).get_case(tenancy.shared_case_id)
    assert stored.version == 1


def test_reads_carry_no_origin_requirement(tenancy: Tenancy) -> None:
    """A GET changes nothing, and the same-origin policy already hides the body."""
    tenancy.sign_in(REVIEWER_EMAIL)

    assert tenancy.get(tenancy.a1()).status_code == 200


# --------------------------------------------------------------------------
# Server-derived reviewer identity
# --------------------------------------------------------------------------


def test_the_persisted_event_names_the_authenticated_user(tenancy: Tenancy) -> None:
    """Read back from the append-only history through the control connection.

    Not from the response: the response is what the API said, and the point is
    what the database holds.
    """
    tenancy.sign_in(REVIEWER_EMAIL)

    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    events = tenancy.auth.repository_for(tenancy.queue_a1).list_events(tenancy.shared_case_id)
    resolutions = [event for event in events if event.is_resolution]
    assert [event.reviewer_id for event in resolutions] == [tenancy.reviewer.user_id]


def test_the_persisted_case_resolution_names_the_authenticated_user(
    tenancy: Tenancy,
) -> None:
    tenancy.sign_in(REVIEWER_EMAIL)

    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    stored = tenancy.auth.repository_for(tenancy.queue_a1).get_case(tenancy.shared_case_id)
    assert stored.case.resolution is not None
    assert stored.case.resolution.reviewer_id == tenancy.reviewer.user_id


def test_two_reviewers_are_told_apart_by_the_session_they_used(tenancy: Tenancy) -> None:
    """Nothing in the request distinguishes them; the cookie does.

    The viewer is promoted so both can decide, and each resolves a different
    case in queue A2. The recorded identities differ because the sessions did.
    """
    tenancy.auth.set_role(tenancy.viewer, tenancy.organization_a, MembershipRole.REVIEWER)
    first, second = tenancy.a2_case_ids[0], tenancy.a2_case_ids[1]

    tenancy.sign_in(REVIEWER_EMAIL)
    assert tenancy.resolve(tenancy.a2(f"/{first}/resolve")).status_code == 200
    tenancy.sign_in(VIEWER_EMAIL)
    assert tenancy.resolve(tenancy.a2(f"/{second}/resolve")).status_code == 200

    repository = tenancy.auth.repository_for(tenancy.queue_a2)

    def recorded(case_id: str) -> str | None:
        stored = repository.get_case(case_id)
        assert stored.case.resolution is not None
        return stored.case.resolution.reviewer_id

    assert recorded(first) == tenancy.reviewer.user_id
    assert recorded(second) == tenancy.viewer.user_id


def test_a_spoofed_reviewer_id_is_refused_and_writes_nothing(tenancy: Tenancy) -> None:
    tenancy.sign_in(REVIEWER_EMAIL)

    response = tenancy.auth.client.post(
        tenancy.a1(f"/{tenancy.shared_case_id}/resolve"),
        json={
            "decision": "NO_MATCH",
            "expected_version": 1,
            "reviewer_id": tenancy.viewer.user_id,
        },
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 422
    assert tenancy.viewer.user_id not in response.text
    stored = tenancy.auth.repository_for(tenancy.queue_a1).get_case(tenancy.shared_case_id)
    assert stored.case.resolution is None


# --------------------------------------------------------------------------
# Queue-local reads: totals, pages, events, suggestions
# --------------------------------------------------------------------------


def test_totals_and_counts_are_queue_local(tenancy: Tenancy) -> None:
    """A1 holds one case and A2 holds three. Neither sees the other's."""
    tenancy.sign_in(REVIEWER_EMAIL)

    first = tenancy.get(tenancy.a1()).json()
    second = tenancy.get(tenancy.a2()).json()

    assert (first["total"], first["count"]) == (1, 1)
    assert (second["total"], second["count"]) == (3, 3)


def test_pagination_is_queue_local(tenancy: Tenancy) -> None:
    """A page of A1 cannot contain, or be shortened by, a case from A2."""
    tenancy.sign_in(REVIEWER_EMAIL)

    page = tenancy.get(f"{tenancy.a1()}?limit=2&offset=0").json()

    assert page["total"] == 1
    assert [item["review_case_id"] for item in page["items"]] == [tenancy.shared_case_id]
    assert not set(tenancy.a2_case_ids) & {item["review_case_id"] for item in page["items"]}


def test_status_filtering_is_queue_local(tenancy: Tenancy) -> None:
    """Resolving every case in A1 does not move A2's filtered totals."""
    tenancy.sign_in(REVIEWER_EMAIL)
    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    assert tenancy.get(f"{tenancy.a1()}?status=PENDING").json()["total"] == 0
    assert tenancy.get(f"{tenancy.a1()}?status=NO_MATCH").json()["total"] == 1
    assert tenancy.get(f"{tenancy.a2()}?status=PENDING").json()["total"] == 3
    assert tenancy.get(f"{tenancy.a2()}?status=NO_MATCH").json()["total"] == 0


def test_events_are_queue_local_for_the_same_case_id(tenancy: Tenancy) -> None:
    """A decision in A1 leaves B1's identically-named case with no history.

    The reviewer is granted membership in B so the comparison is a real 200
    rather than a 404 -- an empty list is a much stronger statement than a
    refusal.
    """
    tenancy.auth.grant(tenancy.reviewer, tenancy.organization_b, MembershipRole.REVIEWER)
    tenancy.sign_in(REVIEWER_EMAIL)
    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    in_a = tenancy.get(tenancy.a1(f"/{tenancy.shared_case_id}/events")).json()
    in_b = tenancy.get(tenancy.b1(f"/{tenancy.shared_case_id}/events")).json()

    assert [event["event_type"] for event in in_a if event["is_resolution"]] == ["NO_MATCH"]
    assert in_b == []


def test_a_resolution_in_one_queue_leaves_the_other_pending(tenancy: Tenancy) -> None:
    tenancy.auth.grant(tenancy.reviewer, tenancy.organization_b, MembershipRole.REVIEWER)
    tenancy.sign_in(REVIEWER_EMAIL)

    tenancy.resolve(tenancy.a1(f"/{tenancy.shared_case_id}/resolve"))

    in_b = tenancy.get(tenancy.b1(f"/{tenancy.shared_case_id}")).json()
    assert in_b["status"] == "PENDING"
    assert in_b["version"] == 1
    assert in_b["resolution"] is None


def test_semantic_suggestions_are_queue_local(tenancy: Tenancy) -> None:
    """An advisory observation stored in A1 is invisible in B1's copy of the case."""
    tenancy.auth.grant(tenancy.reviewer, tenancy.organization_b, MembershipRole.REVIEWER)
    repository = tenancy.auth.repository_for(tenancy.queue_a1)
    stored_case = repository.get_case(tenancy.shared_case_id)
    resolution = make_review_resolution("a-1", "a-2")
    suggestion = make_suggestion(
        stored_case.case, {record.record_id: record for record in resolution.records}
    )
    repository.record_semantic_suggestion(suggestion, now_utc="2026-09-14T08:30:00Z")
    tenancy.sign_in(REVIEWER_EMAIL)

    in_a = tenancy.get(tenancy.a1(f"/{tenancy.shared_case_id}/semantic-suggestions")).json()
    in_b = tenancy.get(tenancy.b1(f"/{tenancy.shared_case_id}/semantic-suggestions")).json()

    assert [item["suggestion_id"] for item in in_a] == [suggestion.suggestion_id]
    assert in_b == []


def test_a_suggestion_stays_advisory(tenancy: Tenancy) -> None:
    """Sprint 09 semantics are untouched: it is marked advisory and decides nothing."""
    repository = tenancy.auth.repository_for(tenancy.queue_a1)
    stored_case = repository.get_case(tenancy.shared_case_id)
    resolution = make_review_resolution("a-1", "a-2")
    suggestion = make_suggestion(
        stored_case.case, {record.record_id: record for record in resolution.records}
    )
    repository.record_semantic_suggestion(suggestion, now_utc="2026-09-14T08:30:00Z")
    tenancy.sign_in(REVIEWER_EMAIL)

    body = tenancy.get(tenancy.a1(f"/{tenancy.shared_case_id}/semantic-suggestions")).json()
    detail = tenancy.get(tenancy.a1(f"/{tenancy.shared_case_id}")).json()

    assert body[0]["advisory"] is True
    assert "explanation" not in body[0]
    assert detail["status"] == "PENDING"


# --------------------------------------------------------------------------
# Optimistic concurrency, unchanged
# --------------------------------------------------------------------------


def test_a_stale_version_is_still_a_409(tenancy: Tenancy) -> None:
    """Tenant authorization does not touch the CAS contract."""
    tenancy.sign_in(REVIEWER_EMAIL)
    url = tenancy.a1(f"/{tenancy.shared_case_id}/resolve")
    assert tenancy.resolve(url).status_code == 200

    stale = tenancy.resolve(url, version=1)

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVIEW_CASE_VERSION_CONFLICT"
    assert stale.json()["error"]["details"] == {"expected_version": 1}


def test_a_terminal_case_is_still_a_409_with_its_own_code(tenancy: Tenancy) -> None:
    """Stale and terminal stay distinguishable, and neither becomes a 403."""
    tenancy.sign_in(REVIEWER_EMAIL)
    url = tenancy.a1(f"/{tenancy.shared_case_id}/resolve")
    tenancy.resolve(url)

    terminal = tenancy.resolve(url, version=2)

    assert terminal.status_code == 409
    assert terminal.json()["error"]["code"] == "REVIEW_CASE_NOT_PENDING"


def test_a_version_conflict_is_not_reported_as_an_authorization_failure(
    tenancy: Tenancy,
) -> None:
    """A REVIEWER who lost a race has not lost a capability."""
    tenancy.sign_in(REVIEWER_EMAIL)
    url = tenancy.a1(f"/{tenancy.shared_case_id}/resolve")
    tenancy.resolve(url)

    stale = tenancy.resolve(url, version=1)

    assert stale.status_code not in (401, 403, 404)


# --------------------------------------------------------------------------
# Sprint 08 MATCH authorization, preserved
# --------------------------------------------------------------------------


def test_a_reviewer_cannot_override_sprint_08_match_authorization(
    tenancy: Tenancy,
) -> None:
    """The layer separation, asserted where it matters most.

    The triangle queue makes a MATCH unsafe through recorded human decisions: a
    NO_MATCH on (a,c) and a MATCH on (a,b) put b and c in one component with an
    already-refused pair. The third MATCH is refused by Sprint 08 -- and it is
    refused with the *domain's* answer, not with a 403, because the reviewer's
    capability was never in question.
    """
    tenancy.sign_in(REVIEWER_EMAIL)
    by_pair = {
        (case.pair.record_a_id, case.pair.record_b_id): case.review_case_id
        for case in tenancy.auth.repository_for(tenancy.queue_a2)
        .load_workflow_bundle()
        .to_workflow_state()
        .cases
    }

    def decide(pair: tuple[str, str], decision: str):
        return tenancy.auth.client.post(
            tenancy.a2(f"/{by_pair[pair]}/resolve"),
            json={"decision": decision, "expected_version": 1},
            headers={"Origin": ALLOWED_ORIGIN},
        )

    assert decide(("rec-a", "rec-c"), "NO_MATCH").status_code == 200
    assert decide(("rec-a", "rec-b"), "MATCH").status_code == 200

    forbidden = decide(("rec-b", "rec-c"), "MATCH")

    assert forbidden.status_code == 409
    assert forbidden.json()["error"]["code"] == "HUMAN_REVIEW_CONTRADICTION"


def test_the_sprint_08_refusal_is_not_a_403(tenancy: Tenancy) -> None:
    """Two authorizations, two vocabularies.

    403 would tell a reviewer their credentials were insufficient, which is
    false: they are permitted to attempt the decision, and the decision itself
    is what the evidence forbids.
    """
    tenancy.sign_in(REVIEWER_EMAIL)
    by_pair = {
        (case.pair.record_a_id, case.pair.record_b_id): case.review_case_id
        for case in tenancy.auth.repository_for(tenancy.queue_a2)
        .load_workflow_bundle()
        .to_workflow_state()
        .cases
    }

    def decide(pair: tuple[str, str], decision: str):
        return tenancy.auth.client.post(
            tenancy.a2(f"/{by_pair[pair]}/resolve"),
            json={"decision": decision, "expected_version": 1},
            headers={"Origin": ALLOWED_ORIGIN},
        )

    decide(("rec-a", "rec-c"), "NO_MATCH")
    decide(("rec-a", "rec-b"), "MATCH")

    forbidden = decide(("rec-b", "rec-c"), "MATCH")

    assert forbidden.status_code not in (401, 403)
    assert forbidden.json()["error"]["code"] != "FORBIDDEN"
