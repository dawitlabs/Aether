import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from neo4j import GraphDatabase

from aether.core.models import Contributor
from aether.storage.contributors import Neo4jContributorStore
from aether.storage.knowledge import Neo4jKnowledgeStore
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


@pytest.fixture
def knowledge(database, document_ids):
    """Depends on document_ids so these records are removed before text units."""
    driver, name = database
    created = []
    yield Neo4jKnowledgeStore(driver, name), created
    driver.execute_query(
        "MATCH (n) WHERE (n:Entity OR n:Relationship OR n:Claim) AND n.id IN $ids "
        "DETACH DELETE n",
        parameters_={"ids": [str(value) for value in created]},
        database_=name,
    )


@pytest.fixture
def make_contributor(database):
    """Create contributors; returns (contributor, key). Removed afterwards."""
    driver, name = database
    store = Neo4jContributorStore(driver, name)
    created = []

    def make(**fields):
        contributor = Contributor(**{"type": "human", "display_name": "test", **fields})
        created.append(str(contributor.id))
        return contributor, store.create(contributor)

    yield make
    driver.execute_query(
        "MATCH (c:Contributor) WHERE c.id IN $ids DETACH DELETE c",
        parameters_={"ids": created}, database_=name,
    )


@pytest.fixture
def auth(make_contributor):
    """Authorization header for an admin who may propose, review, and administer."""
    _, key = make_contributor(permissions=["propose", "review", "admin"])
    return {"Authorization": f"Bearer {key}"}
