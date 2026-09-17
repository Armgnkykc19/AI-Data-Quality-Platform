"""A service that records how it was called, or raises what it was told to.

Two jobs. It captures the exact arguments the route forwards, so "the HTTP layer
adds nothing to the authority call" is asserted against a recorded call rather
than inferred by reading the route. And it raises a chosen exception, so every
Phase C error mapping can be exercised without contriving the domain state that
would produce it naturally -- the real domain states are exercised separately
against SQLite.

It deliberately subclasses nothing and implements only ``resolve_case``. If a
route ever reached for another service method it would get an ``AttributeError``
rather than a quietly working call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from human_review.models import HumanReviewDecision
from review_application import ReviewResolutionResult


@dataclass(frozen=True)
class ResolveCall:
    """Exactly what crossed the boundary into the application layer."""

    review_case_id: str
    decision: HumanReviewDecision
    reviewer_id: str | None
    expected_version: int
    extra_kwargs: dict[str, Any]


class FakeReviewQueueService:
    def __init__(
        self,
        *,
        result: ReviewResolutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[ResolveCall] = []

    def resolve_case(
        self,
        review_case_id: str,
        **kwargs: Any,
    ) -> ReviewResolutionResult:
        # Captured as **kwargs rather than named parameters so a route passing
        # an unexpected extra -- authorization context, a records map, a config
        # path -- is recorded rather than rejected by this signature. A test can
        # then assert the extras are empty, which is the stronger claim.
        self.calls.append(
            ResolveCall(
                review_case_id=review_case_id,
                decision=kwargs.get("decision"),  # type: ignore[arg-type]
                reviewer_id=kwargs.get("reviewer_id"),
                expected_version=kwargs.get("expected_version"),  # type: ignore[arg-type]
                extra_kwargs={
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"decision", "reviewer_id", "expected_version"}
                },
            )
        )
        if self._error is not None:
            raise self._error
        assert self._result is not None, "FakeReviewQueueService needs a result or an error."
        return self._result

    # Anything else a route might reach for is absent on purpose; see the
    # module docstring.
