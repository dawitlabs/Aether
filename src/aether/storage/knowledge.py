"""Neo4j create/read for entities, relationships, and claims with evidence links.

Relationships are stored as nodes, not Neo4j edges, so each one can cite its
own text units. Every cited or linked record must already exist; otherwise the
whole write rolls back.
"""

import json
from collections.abc import Mapping
from typing import Literal, TypeVar
from uuid import UUID

from neo4j import Driver, ManagedTransaction
from neo4j.exceptions import ConstraintError
from pydantic import BaseModel

from aether.core.models import Claim, Entity, Relationship

Label = Literal["TextUnit", "Entity", "Relationship", "Claim"]
# (edge type, target label, target id)
Link = tuple[str, Label, str]
Row = dict[str, object]
M = TypeVar("M", bound=BaseModel)


class DuplicateRecordError(ValueError):
    """A record with the supplied ID already exists."""


class MissingReferenceError(ValueError):
    """A referenced record does not exist; nothing was written."""


def _dump(model: BaseModel, json_fields: tuple[str, ...]) -> Row:
    # Revalidate even if a caller mutated a previously valid model.
    data = type(model).model_validate(model.model_dump()).model_dump(mode="json")
    for field in json_fields:
        data[f"{field}_json"] = json.dumps(
            data.pop(field), ensure_ascii=False, allow_nan=False
        )
    return data


def _load(
    model_type: type[M], data: Mapping[str, object], json_fields: tuple[str, ...], refs: Row
) -> M:
    values = dict(data)
    for field in json_fields:
        raw = values.pop(f"{field}_json")
        if not isinstance(raw, str):
            raise ValueError(f"Stored {field} must be JSON text")
        values[field] = json.loads(raw)
    return model_type.model_validate(values | refs)


def _require(tx: ManagedTransaction, label: Label, ids: set[str]) -> None:
    if not ids:
        return
    # Labels come from the Label literal, never from caller input.
    record = tx.run(
        f"MATCH (n:{label}) WHERE n.id IN $ids RETURN collect(n.id) AS found",
        ids=sorted(ids),
    ).single(strict=True)
    missing = ids - set(record["found"])
    if missing:
        raise MissingReferenceError(f"Missing {label} records: {sorted(missing)}")


def _citation_rows(refs: list[Row]) -> list[Row]:
    return [
        {
            "text_unit_id": ref["text_unit_id"],
            "edge": {k: v for k, v in ref.items() if k != "text_unit_id"} | {"position": i},
        }
        for i, ref in enumerate(refs)
    ]


class Neo4jKnowledgeStore:
    """Use after ensure_schema(); does not open or close the shared driver."""

    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def _create(self, label: Label, node: Row, refs: list[Row], links: list[Link]) -> None:
        def write(tx: ManagedTransaction) -> None:
            _require(tx, "TextUnit", {str(ref["text_unit_id"]) for ref in refs})
            for target in {link[1] for link in links}:
                _require(tx, target, {link[2] for link in links if link[1] == target})
            tx.run(f"CREATE (n:{label}) SET n = $node", node=node).consume()
            tx.run(
                f"MATCH (n:{label} {{id: $id}}) UNWIND $rows AS row "
                "MATCH (t:TextUnit {id: row.text_unit_id}) "
                "CREATE (n)-[c:CITES]->(t) SET c = row.edge",
                id=node["id"], rows=_citation_rows(refs),
            ).consume()
            for edge_type, target, target_id in links:
                tx.run(
                    f"MATCH (n:{label} {{id: $id}}), (m:{target} {{id: $target_id}}) "
                    f"CREATE (n)-[:{edge_type}]->(m)",
                    id=node["id"], target_id=target_id,
                ).consume()

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(write)
        except ConstraintError as error:
            raise DuplicateRecordError(f"{label} {node['id']} already exists") from error

    def _get(self, label: Label, record_id: UUID) -> tuple[Row, list[Row]] | None:
        records, _, _ = self._driver.execute_query(
            f"MATCH (n:{label} {{id: $id}}) "
            "OPTIONAL MATCH (n)-[c:CITES]->(t:TextUnit) "
            "WITH n, c, t ORDER BY c.position "
            "RETURN properties(n) AS data, collect(c {.*, text_unit_id: t.id}) AS refs",
            parameters_={"id": str(record_id)},
            database_=self._database,
            routing_="r",
        )
        if not records:
            return None
        refs = [{k: v for k, v in ref.items() if k != "position"} for ref in records[0]["refs"]]
        return records[0]["data"], refs

    def create_entity(self, entity: Entity) -> None:
        node = _dump(entity, ("properties",))
        self._create("Entity", node, node.pop("provenance"), [])

    def get_entity(self, entity_id: UUID) -> Entity | None:
        found = self._get("Entity", entity_id)
        if found is None:
            return None
        return _load(Entity, found[0], ("properties",), {"provenance": found[1]})

    def create_relationship(self, relationship: Relationship) -> None:
        node = _dump(relationship, ("properties",))
        links: list[Link] = [
            ("FROM", "Entity", str(node["source_id"])),
            ("TO", "Entity", str(node["target_id"])),
        ]
        self._create("Relationship", node, node.pop("provenance"), links)

    def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        found = self._get("Relationship", relationship_id)
        if found is None:
            return None
        return _load(Relationship, found[0], ("properties",), {"provenance": found[1]})

    def create_claim(self, claim: Claim) -> None:
        # ponytail: blanket block until the contribution review workflow exists.
        if claim.status == "verified" or claim.verified_at or claim.verified_by:
            raise ValueError("Claims cannot be stored as verified without review")
        node = _dump(claim, ("contributors",))
        refs = [*node.pop("evidence"), *node.pop("counter_evidence")]
        edges = (
            ("SUBJECT", "Entity", "subject_id"),
            ("OBJECT", "Entity", "object_id"),
            ("SUPERSEDES", "Claim", "supersedes_id"),
            ("SUPERSEDED_BY", "Claim", "superseded_by_id"),
        )
        links: list[Link] = [
            (edge, target, str(node[field])) for edge, target, field in edges if node[field]
        ]
        self._create("Claim", node, refs, links)

    def get_claim(self, claim_id: UUID) -> Claim | None:
        found = self._get("Claim", claim_id)
        if found is None:
            return None
        data, refs = found
        evidence = {
            "evidence": [ref for ref in refs if ref["supports"]],
            "counter_evidence": [ref for ref in refs if not ref["supports"]],
        }
        return _load(Claim, data, ("contributors",), evidence)
