import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from neo4j import GraphDatabase

from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore


@pytest.fixture
def database():
    if os.environ.get("AETHER_TEST_NEO4J") != "1":
        pytest.skip("Set AETHER_TEST_NEO4J=1 to run local database integration tests")
    root = Path(__file__).resolve().parents[2]
    config = {**dotenv_values(root / ".env"), **os.environ}
    # Tests clean up only UUID-scoped records, and never target a remote server.
    assert config["NEO4J_URI"] == "bolt://127.0.0.1:7687"
    with GraphDatabase.driver(
        config["NEO4J_URI"],
        auth=(config["NEO4J_USERNAME"], config["NEO4J_PASSWORD"]),
        max_transaction_retry_time=0,
    ) as driver:
        driver.verify_connectivity()
        name = config["NEO4J_DATABASE"]
        ensure_schema(driver, name)
        yield driver, name


@pytest.fixture
def store(database):
    driver, name = database
    return Neo4jTextUnitStore(driver, name)


@pytest.fixture
def document_ids(database):
    driver, name = database
    ids = [uuid4(), uuid4()]
    yield ids
    driver.execute_query(
        "MATCH (t:TextUnit) WHERE t.source_document_id IN $ids DELETE t",
        parameters_={"ids": [str(value) for value in ids]},
        database_=name,
    )
