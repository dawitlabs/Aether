"""Check authentication and that Aether records survive a restart; restarts the server.

Removes the records it created.
"""

from hashlib import sha256
from uuid import uuid4

from neo4j import GraphDatabase
from neo4j import __version__ as driver_version
from neo4j.exceptions import AuthError

import local_neo4j as runtime
from aether.core.models import Claim, Entity, EvidenceRef, ProvenanceRef, Relationship, TextUnit
from aether.storage.knowledge import Neo4jKnowledgeStore
from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore

config = runtime.settings()
database = config["NEO4J_DATABASE"]


def connect(password):
    return GraphDatabase.driver(
        config["NEO4J_URI"],
        auth=(config["NEO4J_USERNAME"], password),
        connection_timeout=2,
        max_transaction_retry_time=0,
    )


try:
    with connect("incorrect-" + str(uuid4())) as driver:
        driver.verify_connectivity()
except AuthError:
    print("PASS: invalid credentials rejected.")
else:
    raise AssertionError("Database accepted invalid credentials")

with connect(config["NEO4J_PASSWORD"]) as driver:
    records, _, _ = driver.execute_query(
        "CALL dbms.components() YIELD versions RETURN versions[0] AS version",
        database_=database,
    )
    print(f"Server: {records[0]['version']}; Python driver: {driver_version}")
    ensure_schema(driver, database)
    units, knowledge = Neo4jTextUnitStore(driver, database), Neo4jKnowledgeStore(driver, database)
    text = "Nix provides reproducible development shells."
    unit = units.create(TextUnit(
        text=text, source_document_id=uuid4(), token_count=6, media_type="text/plain",
        content_hash=sha256(text.encode("utf-8")).hexdigest(), metadata={"probe": True},
    ))
    cite = ProvenanceRef(text_unit_id=unit.id, excerpt="Nix", extracted_by="restart-check")
    nix, shell = (
        Entity(name=name, type="probe", description="Restart probe.", confidence=1,
               provenance=[cite])
        for name in ("Nix", "Development shell")
    )
    relationship = Relationship(
        source_id=nix.id, target_id=shell.id, type="PROVIDES",
        description="Restart probe.", weight=1, provenance=[cite],
    )
    claim = Claim(
        statement=text, subject_id=nix.id, object_id=shell.id, confidence=0.9,
        evidence=[EvidenceRef(text_unit_id=unit.id, supports=True, added_by="restart-check")],
    )
    knowledge.create_entity(nix)
    knowledge.create_entity(shell)
    knowledge.create_relationship(relationship)
    knowledge.create_claim(claim)

try:
    runtime.command("neo4j", "stop")
    runtime.command("neo4j", "start")
    runtime.check(wait=True)
    with connect(config["NEO4J_PASSWORD"]) as driver:
        units, knowledge = Neo4jTextUnitStore(driver, database), Neo4jKnowledgeStore(driver, database)
        assert units.get(unit.id) == unit
        assert knowledge.get_entity(nix.id) == nix
        assert knowledge.get_entity(shell.id) == shell
        assert knowledge.get_relationship(relationship.id) == relationship
        assert knowledge.get_claim(claim.id) == claim
        print("PASS: text unit, entities, relationship, and claim survived a restart.")
finally:
    with connect(config["NEO4J_PASSWORD"]) as driver:
        driver.execute_query(
            "MATCH (n) WHERE (n:Entity OR n:Relationship OR n:Claim OR n:TextUnit) "
            "AND n.id IN $ids DETACH DELETE n",
            ids=[str(x.id) for x in (claim, relationship, nix, shell, unit)],
            database_=database,
        )
