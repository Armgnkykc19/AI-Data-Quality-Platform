"""HTTP route modules.

Each module here declares an ``APIRouter`` and nothing else: importing one must
not open storage, read configuration, or contact anything. Routers are attached
to an application by ``review_api.app.create_app``.

No module in this package may import ``review_persistence``, ``sqlite3``, the
Sprint 08 authorization internals, or any semantic-review provider. Routes speak
to ``review_application`` through the seam in ``review_api.dependencies``.
"""
