"""Application layer for the persistent review queue.

Orchestrates persistence around the unchanged Sprint 08 Human Review domain.
``ReviewWorkflow.resolve_case`` remains the only owner of decision transitions;
nothing here reimplements MATCH authorization.

``ReviewQueueService`` is the supported entry point for resolving a persisted
case, and ``register_review_workflow`` is the supported entry point for filling
a queue with cases to decide. Both are exported from the package root, while
the repository Protocol they depend on is implemented in ``review_persistence``.

``ReviewQueue`` is the aggregate an organization owns and the boundary every
review record is scoped to; see ``review_application.queues`` for why the queue
rather than the organization is the direct owner.
"""

from review_application.bootstrap import (
    ReviewWorkflowRegistration,
    register_review_workflow,
)
from review_application.errors import (
    DuplicateCaseRegistrationError,
    PersistedCaseIntegrityError,
    ReviewApplicationError,
    ReviewAuthorizationConfigError,
    ReviewCaseNotFoundError,
    ReviewConflictError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
    ReviewQueueNotFoundError,
    ReviewSchemaMigrationRequiredError,
    ReviewSchemaVersionError,
    SemanticSuggestionConflictError,
    SemanticSuggestionIntegrityError,
)
from review_application.history import (
    ReconstructedHistory,
    audit_entry_from_payload,
    reconstruct_history,
)
from review_application.models import (
    PersistedCase,
    ReviewEvent,
    ReviewEventType,
    WorkflowBundle,
)
from review_application.queues import (
    REVIEW_QUEUE_ID_PREFIX,
    ReviewQueue,
    new_review_queue_id,
)
from review_application.repository import ReviewCaseRepository
from review_application.service import ReviewQueueService, ReviewResolutionResult

__all__ = [
    "REVIEW_QUEUE_ID_PREFIX",
    "DuplicateCaseRegistrationError",
    "PersistedCase",
    "PersistedCaseIntegrityError",
    "ReconstructedHistory",
    "ReviewApplicationError",
    "ReviewAuthorizationConfigError",
    "ReviewCaseNotFoundError",
    "ReviewCaseRepository",
    "ReviewConflictError",
    "ReviewEvent",
    "ReviewEventIntegrityError",
    "ReviewEventType",
    "ReviewPersistenceError",
    "ReviewQueue",
    "ReviewQueueNotFoundError",
    "ReviewQueueService",
    "ReviewResolutionResult",
    "ReviewSchemaMigrationRequiredError",
    "ReviewSchemaVersionError",
    "ReviewWorkflowRegistration",
    "SemanticSuggestionConflictError",
    "SemanticSuggestionIntegrityError",
    "WorkflowBundle",
    "audit_entry_from_payload",
    "new_review_queue_id",
    "reconstruct_history",
    "register_review_workflow",
]
