import pytest

from ingestion.config import load_ingestion_config
from schema_mapping.config import load_schema_mapping_config


@pytest.fixture
def mapping_config():
    return load_schema_mapping_config()


@pytest.fixture
def ingestion_config():
    return load_ingestion_config()
