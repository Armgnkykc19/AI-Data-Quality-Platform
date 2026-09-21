"""Typed errors for the review application and persistence layers.

Sprint 08 domain errors stay owned by ``human_review``. They are never wrapped
into persistence errors, because a caller must be able to distinguish "the
domain refused this decision" from "storage failed".
"""

from __future__ import annotations

# Re-exported rather than redefined: a second class with the same name would
# break ``except`` clauses written against either layer. Callers get one import
# site; the class identity remains the Sprint 08 domain error.
from human_review.errors import ReviewCaseNotFoundError

__all__ = [
    "DuplicateCaseRegistrationError",
    "ReviewAuthorizationConfigError",
    "PersistedCaseIntegrityError",
    "ReviewApplicationError",
    "ReviewCaseNotFoundError",
    "ReviewConflictError",
    "ReviewEventIntegrityError",
    "ReviewPersistenceConfigurationError",
    "ReviewPersistenceError",
    "ReviewQueueNotFoundError",
    "ReviewSchemaMigrationRequiredError",
    "ReviewSchemaVersionError",
    "ReviewWorkflowContextConflictError",
    "ReviewWorkflowContextMissingError",
    "SemanticSuggestionConflictError",
    "SemanticSuggestionIntegrityError",
]


class ReviewApplicationError(Exception):
    """Base error for the review application layer."""


class PersistedCaseIntegrityError(ReviewApplicationError):
    """Persistence metadata contradicts the wrapped domain case."""


class ReviewEventIntegrityError(ReviewApplicationError):
    """A history event does not satisfy its event-type invariants."""


class ReviewConflictError(ReviewApplicationError):
    """Optimistic-concurrency conflict: another writer advanced the case.

    Deliberately not a ``ReviewPersistenceError``. Storage did not fail; the
    caller simply lost a race and must reload before retrying. Retrying is the
    caller's decision, because a retry re-runs Sprint 08 authorization against
    a state the caller has not yet seen.
    """

    def __init__(self, message: str, *, review_case_id: str, expected_version: int) -> None:
        super().__init__(message)
        self.review_case_id = review_case_id
        self.expected_version = expected_version


class DuplicateCaseRegistrationError(ReviewApplicationError):
    """A registered case contradicts a stored case with the same identity.

    Not raised for benign re-registration. Re-registering an already stored
    deterministic case is an idempotent no-op by design; this error signals a
    genuine identity contradiction, such as the same review_case_id arriving
    with a different record pair.
    """


class ReviewAuthorizationConfigError(ReviewApplicationError):
    """The entity-resolution config a queue was generated with cannot be loaded.

    Raised instead of falling back to the default config. The config decides
    what counts as a severe identity conflict, so authorizing against a
    different one could permit a merge the real configuration forbids.

    Deliberately not a Sprint 08 domain error: the domain refused nothing here,
    and a caller must be able to tell "this MATCH is unsafe" from "the material
    needed to judge it is unavailable".
    """


class SemanticSuggestionIntegrityError(ReviewApplicationError):
    """An advisory suggestion is malformed, or contradicts the case it names.

    Covers a suggestion that is not a Sprint 09 object, one whose content
    address does not match its own content, one naming a different case or a
    different record pair than the stored case, and a stored row that no longer
    agrees with its payload.

    Deliberately not a ``ReviewPersistenceError``: storage worked. The object
    offered to it was not a faithful Sprint 09 observation, and a caller needs
    to tell those apart to know whether retrying could ever help.
    """


class SemanticSuggestionConflictError(ReviewApplicationError):
    """Two different observations claim one content-addressed suggestion id.

    Sprint 09 ids are derived from content, so an identical replay is an
    idempotent no-op. Different content under the same id means one of the two
    is not what it claims to be, and a stored suggestion is immutable -- so the
    write is refused rather than resolved in either direction.

    Distinct from ``ReviewConflictError``: no version was raced and nothing
    needs reloading. Distinct from ``ReviewPersistenceError``: nothing failed.
    """


class ReviewPersistenceError(ReviewApplicationError):
    """Storage-level failure while reading or writing review state."""


class ReviewSchemaVersionError(ReviewPersistenceError):
    """The database declares a schema version this build does not support."""


class ReviewSchemaMigrationRequiredError(ReviewSchemaVersionError):
    """The database is a known earlier schema that must be migrated explicitly.

    A subclass rather than a sibling, so every existing handler that answers a
    schema problem -- including the API's 503 mapping -- keeps answering this
    one unchanged. What the subclass adds is the distinction an operator needs:
    a database this build once wrote and can be migrated forward, as opposed to
    one written by a build this code has never heard of.

    Raised on open and never acted on. Nothing upgrades a database in place,
    drops a table, or recreates a file; a review queue holds human decisions,
    and guessing is worse than refusing to open.
    """

    def __init__(self, message: str, *, stored_version: str, required_version: str) -> None:
        super().__init__(message)
        self.stored_version = stored_version
        self.required_version = required_version


class ReviewQueueNotFoundError(ReviewApplicationError):
    """The review queue a write was addressed to is not stored.

    Raised before any review row is written, so a workflow can never be
    registered into a queue that does not exist -- which, with tenant ownership
    flowing through the queue, would be review data owned by nobody.
    """


class ReviewPersistenceConfigurationError(ReviewApplicationError):
    """Review persistence configuration is missing, malformed, or unsupported.

    Deliberately not a ``ReviewPersistenceError``: it is raised before any
    database is touched, so reporting it as a storage failure would send an
    operator looking at the wrong thing.
    """


class ReviewWorkflowContextMissingError(ReviewApplicationError):
    """The database holds review cases but no workflow authorization context.

    Raised instead of returning a partial bundle. Sprint 08 MATCH
    authorization projects component membership across every record and every
    AUTO_MATCH edge, so a bundle without them would let a check that should
    fail closed pass against a partial graph.
    """


class ReviewWorkflowContextConflictError(ReviewApplicationError):
    """A registration contradicts the stored workflow authorization context.

    Distinct from DuplicateCaseRegistrationError: the conflict is the global
    record set or AUTO_MATCH snapshot, not one case identity. Replacing the
    stored context would silently re-evaluate already-recorded human decisions
    against a different graph, so registration fails closed instead.
    """
