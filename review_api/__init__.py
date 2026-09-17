"""HTTP surface for the persistent review queue.

Sprint 11 Phase A is the foundation only: an application factory, the
production composition seam, a stable error envelope, and ``GET /health``. No
review-case endpoint exists yet -- listing, detail, history, semantic
suggestions, and resolution arrive in Phase B and Phase C, each with the tests
that prove Sprint 08 remains the authority on every human decision.

Three properties hold at this layer and are asserted, not assumed, by
``tests/review_api/test_api_layering.py``.

Importing this package opens no database, reads no configuration file, creates
no ``storage/review_queue.db``, and contacts no network or model provider.
Storage belongs to the production lifespan, not to ``create_app`` and not to
import time.

The HTTP layer owns no domain logic. It holds no authorization rule, no
transition rule, and no SQL; it calls ``review_application`` and reports what
came back. Only ``review_api.dependencies`` knows that the queue is stored in
SQLite.

This API has no authentication, no verified reviewer identity, no tenant
isolation, no rate limiting, and no CORS policy. It is a local reviewer tool,
bound to localhost, and it is not internet-ready. Sprint 13 owns that boundary.
"""

from review_api.app import create_app, create_production_app

__all__ = ["create_app", "create_production_app"]
