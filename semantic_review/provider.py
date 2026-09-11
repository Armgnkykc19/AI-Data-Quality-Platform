from __future__ import annotations

from typing import Protocol

from semantic_review.models import SemanticReviewRequest, SemanticSuggestion


class SemanticReviewProvider(Protocol):
    provider_name: str
    live: bool

    def suggest(self, request: SemanticReviewRequest) -> SemanticSuggestion:
        """Return an advisory suggestion. Must not mutate review or canonical state."""
