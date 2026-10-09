"""Neo4j persistence for communities: (Entity)-[:IN_COMMUNITY]->(Community)."""

import json
from typing import NamedTuple
from uuid import UUID

from neo4j import Driver, ManagedTransaction

from aether.core.models import Community, Finding

Row = dict[str, object]


class CommunityContext(NamedTuple):
    entities: list[Row]
    relationships: list[Row]
    text_units: list[Row]


class Neo4jCommunityStore:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def entity_graph(self) -> tuple[list[str], list[tuple[str, str, float]]]:
        """Active entity IDs and weighted edges between active entities."""
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity {status: 'active'}) WITH collect(e.id) AS nodes "
            "OPTIONAL MATCH (a:Entity {status: 'active'})<-[:FROM]-"
            "(r:Relationship {status: 'active'})-[:TO]->(b:Entity {status: 'active'}) "
            "RETURN nodes, collect([a.id, b.id, r.weight]) AS edges",
            database_=self._database,
            routing_="r",
        )
        nodes, edges = records[0]["nodes"], records[0]["edges"]
        return nodes, [(a, b, float(w)) for a, b, w in edges]

    def replace_all(self, communities: list[Community]) -> None:
        rows = [
            {"id": str(c.id), "created_at": c.created_at.isoformat(),
             "entity_ids": [str(e) for e in c.entity_ids]}
            for c in communities
        ]

        def write(tx: ManagedTransaction) -> None:
            tx.run("MATCH (c:Community) DETACH DELETE c").consume()
            tx.run(
                "UNWIND $rows AS row "
                "CREATE (c:Community {id: row.id, created_at: row.created_at}) "
                "WITH c, row UNWIND row.entity_ids AS entity_id "
                "MATCH (e:Entity {id: entity_id}) CREATE (e)-[:IN_COMMUNITY]->(c)",
                rows=rows,
            ).consume()

        with self._driver.session(database=self._database) as session:
            session.execute_write(write)

    def _read(self, query: str, **params: object) -> list[Row]:
        records, _, _ = self._driver.execute_query(
            query, parameters_=params, database_=self._database, routing_="r"
        )
        return [record.data() for record in records]

    def list_all(self) -> list[Community]:
        rows = self._read(
            "MATCH (e:Entity)-[:IN_COMMUNITY]->(c:Community) "
            "WITH c, e ORDER BY e.id "
            "RETURN c.id AS id, c.created_at AS created_at, collect(e.id) AS entity_ids, "
            "c.title AS title, c.summary AS summary, c.findings_json AS findings_json "
            "ORDER BY size(entity_ids) DESC, id"
        )
        for row in rows:
            row["findings"] = json.loads(str(row.pop("findings_json") or "[]"))
        return [Community.model_validate(row) for row in rows]

    def context(self, community_id: UUID, *, max_units: int = 12) -> CommunityContext:
        """Members, relationships among them, and the text units they cite most."""
        params = {"id": str(community_id)}
        entities = self._read(
            "MATCH (e:Entity)-[:IN_COMMUNITY]->(:Community {id: $id}) "
            "RETURN e.id AS id, e.name AS name, e.type AS type, e.description AS description "
            "ORDER BY e.name, e.id",
            **params,
        )
        relationships = self._read(
            "MATCH (c:Community {id: $id})<-[:IN_COMMUNITY]-(a:Entity)<-[:FROM]-"
            "(r:Relationship {status: 'active'})-[:TO]->(b:Entity)-[:IN_COMMUNITY]->(c) "
            "RETURN a.name AS source, r.type AS type, b.name AS target, "
            "r.description AS description ORDER BY r.weight DESC, r.id LIMIT 50",
            **params,
        )
        text_units = self._read(
            "MATCH (c:Community {id: $id})<-[:IN_COMMUNITY]-(e:Entity) "
            "OPTIONAL MATCH (e)<-[:FROM|TO]-(r:Relationship) "
            "WITH collect(DISTINCT e) + collect(DISTINCT r) AS records "
            "UNWIND records AS n MATCH (n)-[:CITES]->(t:TextUnit) "
            "RETURN t.id AS id, t.text AS text, count(*) AS citations "
            "ORDER BY citations DESC, id LIMIT $limit",
            limit=max_units, **params,
        )
        return CommunityContext(entities, relationships, text_units)

    def set_report(
        self, community_id: UUID, title: str, summary: str, findings: list[Finding]
    ) -> None:
        self._driver.execute_query(
            "MATCH (c:Community {id: $id}) "
            "SET c.title = $title, c.summary = $summary, c.findings_json = $findings",
            parameters_={
                "id": str(community_id), "title": title, "summary": summary,
                "findings": json.dumps([f.model_dump(mode="json") for f in findings]),
            },
            database_=self._database,
        )

