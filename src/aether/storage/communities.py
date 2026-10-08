"""Neo4j persistence for communities: (Entity)-[:IN_COMMUNITY]->(Community)."""

from neo4j import Driver, ManagedTransaction

from aether.core.models import Community


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

    def list_all(self) -> list[Community]:
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity)-[:IN_COMMUNITY]->(c:Community) "
            "WITH c, e ORDER BY e.id "
            "RETURN c.id AS id, c.created_at AS created_at, collect(e.id) AS entity_ids "
            "ORDER BY size(entity_ids) DESC, id",
            database_=self._database,
            routing_="r",
        )
        return [Community.model_validate(record.data()) for record in records]

