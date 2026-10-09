"""Read-side graph queries: entity search and one-hop neighborhoods.

Each call issues a fixed number of queries, never one per record.
"""

from typing import NamedTuple
from uuid import UUID

from neo4j import Driver

from aether.core.models import Entity, Relationship, TextUnit
from aether.core.text import name_key
from aether.storage.knowledge import M, _load
from aether.storage.text_units import _model as load_text_unit


class Neighborhood(NamedTuple):
    entity: Entity
    neighbors: list[Entity]
    relationships: list[Relationship]
    text_units: list[TextUnit]


class Neo4jGraphReader:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def _read(self, query: str, **params: object) -> list[dict[str, object]]:
        records, _, _ = self._driver.execute_query(
            query, parameters_=params, database_=self._database, routing_="r"
        )
        return [record.data() for record in records]

    def _load_many(self, model: type[M], label: str, ids: list[str]) -> list[M]:
        rows = self._read(
            f"MATCH (n:{label}) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[c:CITES]->(t:TextUnit) "
            "WITH n, c, t ORDER BY c.position "
            "RETURN properties(n) AS data, collect(c {.*, text_unit_id: t.id}) AS refs",
            ids=ids,
        )
        by_id = {
            row["data"]["id"]: _load(model, row["data"], ("properties",), {"provenance": [
                {k: v for k, v in ref.items() if k != "position"} for ref in row["refs"]
            ]})
            for row in rows
        }
        return [by_id[i] for i in ids if i in by_id]

    def search_entities(self, name: str, *, limit: int = 20) -> list[Entity]:
        # ponytail: CONTAINS scans all entities; add a full-text index when slow.
        rows = self._read(
            "MATCH (e:Entity {status: 'active'}) WHERE e.name_key CONTAINS $q "
            "RETURN e.id AS id ORDER BY size(e.name_key), e.name_key LIMIT $limit",
            q=name_key(name), limit=limit,
        )
        return self._load_many(Entity, "Entity", [row["id"] for row in rows])

    def neighborhood(self, entity_id: UUID, *, limit: int = 50) -> Neighborhood | None:
        rows = self._read(
            # A merged entity shows the neighborhood of the entity it merged into.
            "MATCH (x:Entity {id: $id}) "
            "OPTIONAL MATCH (m:Entity {id: x.merged_into_id}) WITH coalesce(m, x) AS e "
            "OPTIONAL MATCH (e)<-[:FROM|TO]-(r:Relationship)-[:FROM|TO]->(o:Entity) "
            "WHERE o <> e "
            "WITH e, r, o ORDER BY r.weight DESC LIMIT $limit "
            "RETURN e.id AS id, collect(r.id) AS rels, collect(DISTINCT o.id) AS others",
            id=str(entity_id), limit=limit,
        )
        if not rows:
            return None
        entities = self._load_many(Entity, "Entity", [rows[0]["id"], *rows[0]["others"]])
        relationships = self._load_many(Relationship, "Relationship", rows[0]["rels"])
        unit_ids = list(dict.fromkeys(
            str(ref.text_unit_id)
            for record in [entities[0], *relationships]
            for ref in record.provenance
        ))
        return Neighborhood(
            entity=entities[0],
            neighbors=entities[1:],
            relationships=relationships,
            text_units=self.text_units(unit_ids),
        )

    def text_units(self, ids: list[str]) -> list[TextUnit]:
        """Units in the order of ids; unknown IDs are skipped."""
        units = {
            row["data"]["id"]: load_text_unit(row["data"])
            for row in self._read(
                "MATCH (t:TextUnit) WHERE t.id IN $ids RETURN properties(t) AS data", ids=ids
            )
        }
        return [units[i] for i in ids if i in units]
