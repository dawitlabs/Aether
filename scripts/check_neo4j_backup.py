"""Check backup/restore round trip; stops and restarts the local server.

Run with Neo4j started. Removes its probe and the backups it created.
"""

import shutil
from uuid import uuid4

from neo4j import GraphDatabase

import local_neo4j as runtime

config = runtime.settings()
probe_id = str(uuid4())


def query(cypher):
    with GraphDatabase.driver(
        config["NEO4J_URI"], auth=(config["NEO4J_USERNAME"], config["NEO4J_PASSWORD"])
    ) as driver:
        records, _, _ = driver.execute_query(
            cypher, id=probe_id, database_=config["NEO4J_DATABASE"]
        )
        return records


def cycle(*steps):
    runtime.command("neo4j", "stop")
    for step in steps:
        step()
    runtime.command("neo4j", "start")
    runtime.check(wait=True)


before = set(runtime.BACKUPS.glob("*")) if runtime.BACKUPS.exists() else set()
query("CREATE (:AetherBackupProbe {id: $id})")
backups = []
try:
    cycle(lambda: backups.append(runtime.backup()))
    query("MATCH (p:AetherBackupProbe {id: $id}) DELETE p")
    assert not query("MATCH (p:AetherBackupProbe {id: $id}) RETURN p")
    cycle(lambda: runtime.restore(backups[0]))
    assert len(query("MATCH (p:AetherBackupProbe {id: $id}) RETURN p")) == 1
    print("PASS: deleted record came back after restore.")
finally:
    query("MATCH (p:AetherBackupProbe {id: $id}) DELETE p")
    for path in set(runtime.BACKUPS.glob("*")) - before:
        shutil.rmtree(path)
