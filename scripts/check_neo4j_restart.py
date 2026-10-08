"""Check local Neo4j authentication and restart durability; restarts the server."""

from uuid import uuid4

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError

from neo4j import __version__ as driver_version
import local_neo4j as runtime


config = runtime.settings()
probe_id = str(uuid4())


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
        database_=config["NEO4J_DATABASE"],
    )
    print(f"Server: {records[0]['version']}; Python driver: {driver_version}")
    driver.execute_query(
        "CREATE (:AetherRuntimeProbe {id: $id, value: $value})",
        id=probe_id, value="restart-proof", database_=config["NEO4J_DATABASE"],
    )

try:
    runtime.command("neo4j", "stop")
    runtime.command("neo4j", "start")
    runtime.check(wait=True)
    with connect(config["NEO4J_PASSWORD"]) as driver:
        records, _, _ = driver.execute_query(
            "MATCH (p:AetherRuntimeProbe {id: $id}) RETURN p.value AS value",
            id=probe_id, database_=config["NEO4J_DATABASE"],
        )
        assert len(records) == 1 and records[0]["value"] == "restart-proof"
        print("PASS: stored record survived a database restart.")
finally:
    with connect(config["NEO4J_PASSWORD"]) as driver:
        driver.execute_query(
            "MATCH (p:AetherRuntimeProbe {id: $id}) DELETE p",
            id=probe_id, database_=config["NEO4J_DATABASE"],
        )
