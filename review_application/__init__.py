"""Application layer for the persistent review queue.

Orchestrates persistence around the unchanged Sprint 08 Human Review domain.
``ReviewWorkflow.resolve_case`` remains the only owner of decision transitions;
nothing here reimplements MATCH authorization.

``ReviewQueueService`` is the supported entry point for resolving a persisted
case. It is exported from the package root, while the repository Protocol it
depends on is implemented in ``review_persistence``.
"""

from review_application.errors import (
    DuplicateCaseRegistrationError,
    PersistedCaseIntegrityError,
    ReviewApplicationError,
    ReviewAuthorizationConfigError,
    ReviewCaseNotFoundError,
    ReviewConflictError,
    ReviewEventIntegrityError,
    ReviewPersistenceError,
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
from review_application.repository import ReviewCaseRepository
from review_application.service import ReviewQueueService, ReviewResolutionResult

__all__ = [
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
    "ReviewQueueService",
    "ReviewResolutionResult",
    "ReviewSchemaVersionError",
    "SemanticSuggestionConflictError",
    "SemanticSuggestionIntegrityError",
    "WorkflowBundle",
    "audit_entry_from_payload",
    "reconstruct_history",
]
