"""Authenticated, tenant-scoped HTTP surface for the persistent review queue.

Every review operation names its tenant in the URL::

    /api/v1/organizations/{organization_id}/review-queues/{review_queue_id}/review-cases

and every one of them requires a session. There is no unscoped review route, no
route that infers a queue from the caller, and no route that serves a queue
because the installation happens to hold only one. The application supports as
many organizations and queues as an operator created.

Three questions, three layers, deliberately never collapsed:

* ``review_api.security`` resolves the session cookie into a principal --
  *who is this*.
* ``review_api.tenancy`` reads current organization membership and the queue's
  ownership -- *may they reach this queue, and do this*.
* ``human_review`` decides whether a particular MATCH is safe -- *is this merge
  permitted by the review evidence*. A REVIEWER role does not influence it.

Four properties hold at this layer and are asserted, not assumed, by
``tests/review_api/test_api_layering.py``.

Importing this package opens no database, reads no configuration file, creates
no ``storage/review_queue.db``, and contacts no network or model provider.
Storage belongs to the production lifespan, not to ``create_app`` and not to
import time.

The HTTP layer owns no domain logic. It holds no transition rule and no SQL; it
calls ``review_application`` and reports what came back. Only
``review_api.dependencies`` knows that the queue is stored in SQLite.

Reviewer identity is never accepted from a client. The resolution request
carries a decision and a version; the server supplies the authenticated user's
id to the domain, so a durable audit row names the session that was used.

An error tells a caller nothing about a tenant they cannot reach. An unknown
organization, an unknown queue, a queue owned by someone else, and a missing
membership are one 404 with one body.

What is still missing, and named so it is not mistaken for present: no rate
limiting, no TLS termination, no CORS policy (deliberately -- the browser
reaches this API same-origin), and no frontend that speaks these routes yet.
The API is bound to localhost. Sprint 14 owns deployment.
"""

from review_api.app import create_app, create_production_app

__all__ = ["create_app", "create_production_app"]
