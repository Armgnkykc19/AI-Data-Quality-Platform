"""SQLite persistence for the review queue.

The schema contract and configuration live here; the ``sqlite`` subpackage
holds the connection factory, mapper, and repository. Importing the subpackage
explicitly keeps the schema contract usable without opening a database.
"""

from review_persistence.config import (
    DEFAULT_REVIEW_PERSISTENCE_CONFIG,
    SUPPORTED_JOURNAL_MODES,
    ReviewPersistenceConfig,
    load_review_persistence_config,
)
from review_persistence.schema import (
    DATABASE_SCHEMA_VERSION,
    MIGRATION_REQUIRED_SCHEMA_VERSIONS,
    SUPPORTED_DATABASE_SCHEMA_VERSIONS,
    assert_supported_schema_version,
)

__all__ = [
    "DATABASE_SCHEMA_VERSION",
    "DEFAULT_REVIEW_PERSISTENCE_CONFIG",
    "MIGRATION_REQUIRED_SCHEMA_VERSIONS",
    "SUPPORTED_DATABASE_SCHEMA_VERSIONS",
    "SUPPORTED_JOURNAL_MODES",
    "ReviewPersistenceConfig",
    "assert_supported_schema_version",
    "load_review_persistence_config",
]
