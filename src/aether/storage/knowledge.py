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

from aether.core.models import Claim, Entity, ProvenanceRef, Relationship
from aether.core.text import name_key

Label = Literal["TextUnit", "Entity", "Relationship", "Claim", "Contributor"]
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


def _write(
    tx: ManagedTransaction, label: Label, node: Row, refs: list[Row], links: list[Link]
) -> None:
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


def _entity_node(entity: Entity) -> tuple[Row, list[Row]]:
    node = _dump(entity, ("properties",))
    # Exact-match key for resolution; Entity ignores it on read.
    node["name_key"] = name_key(entity.name)
    return node, node.pop("provenance")


def _relationship_node(relationship: Relationship) -> tuple[Row, list[Row], list[Link]]:
    node = _dump(relationship, ("properties",))
    links: list[Link] = [
        ("FROM", "Entity", str(node["source_id"])),
        ("TO", "Entity", str(node["target_id"])),
    ]
    return node, node.pop("provenance"), links


def claim_node(claim: Claim) -> tuple[Row, list[Row], list[Link]]:
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
    return node, refs, links


class Neo4jKnowledgeStore:
    """Use after ensure_schema(); does not open or close the shared driver."""

    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def _create(self, label: Label, node: Row, refs: list[Row], links: list[Link]) -> None:
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(_write, label, node, refs, links)
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
        node, refs = _entity_node(entity)
        self._create("Entity", node, refs, [])

    def get_entity(self, entity_id: UUID) -> Entity | None:
        found = self._get("Entity", entity_id)
        if found is None:
            return None
        return _load(Entity, found[0], ("properties",), {"provenance": found[1]})

    def find_entity(self, name: str, entity_type: str) -> UUID | None:
        """Oldest active entity with this exact name key and type."""
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity {name_key: $key, type: $type, status: 'active'}) "
            "RETURN e.id AS id ORDER BY e.first_seen, e.id LIMIT 1",
            parameters_={"key": name_key(name), "type": entity_type},
            database_=self._database,
            routing_="r",
        )
        return UUID(records[0]["id"]) if records else None

    def active_entity_ids(self, ids: list[UUID], entity_type: str) -> set[UUID]:
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity {type: $type, status: 'active'}) WHERE e.id IN $ids "
            "RETURN collect(e.id) AS ids",
            parameters_={"ids": [str(i) for i in ids], "type": entity_type},
            database_=self._database,
            routing_="r",
        )
        return {UUID(i) for i in records[0]["ids"]}

    def is_extracted(self, text_unit_id: UUID, marker: str) -> bool:
        records, _, _ = self._driver.execute_query(
            "MATCH (x:Extraction {key: $key}) RETURN count(x) > 0 AS done",
            parameters_={"key": f"{text_unit_id}|{marker}"},
            database_=self._database,
            routing_="r",
        )
        return records[0]["done"]

    def extraction_progress(self, document_id: UUID, marker: str) -> tuple[int, int]:
        """(extracted, total) text units of a document for this marker."""
        records, _, _ = self._driver.execute_query(
            "MATCH (t:TextUnit {source_document_id: $doc}) "
            "RETURN count(t) AS total, "
            "count(CASE WHEN EXISTS { (t)<-[:OF]-(:Extraction {marker: $marker}) } "
            "THEN 1 END) AS done",
            parameters_={"doc": str(document_id), "marker": marker},
            database_=self._database,
            routing_="r",
        )
        return records[0]["done"], records[0]["total"]

    def write_extraction(
        self,
        text_unit_id: UUID,
        marker: str,
        entities: list[Entity],
        citations: list[tuple[UUID, ProvenanceRef]],
        relationships: list[Relationship],
    ) -> None:
        """Write one text unit's extraction atomically, with its done-marker.

        citations append provenance to entities that already exist. Raises
        DuplicateRecordError if this unit was already extracted with marker.
        """
        key = f"{text_unit_id}|{marker}"
        cited = [
            {"entity_id": str(entity_id), **ref.model_dump(mode="json")}
            for entity_id, ref in citations
        ]

        def write(tx: ManagedTransaction) -> None:
            _require(tx, "TextUnit", {str(text_unit_id)})
            tx.run(
                "MATCH (t:TextUnit {id: $unit}) "
                "CREATE (x:Extraction {key: $key, marker: $marker, created_at: datetime()}) "
                "CREATE (x)-[:OF]->(t)",
                unit=str(text_unit_id), key=key, marker=marker,
            ).consume()
            for entity in entities:
                node, refs = _entity_node(entity)
                _write(tx, "Entity", node, refs, [])
            _require(tx, "Entity", {row["entity_id"] for row in cited})
            _require(tx, "TextUnit", {row["text_unit_id"] for row in cited})
            # Sequential rows, so each new citation sees the previous ones' count.
            for row in cited:
                edge = {k: v for k, v in row.items() if k not in ("entity_id", "text_unit_id")}
                tx.run(
                    "MATCH (e:Entity {id: $entity}), (t:TextUnit {id: $unit}) "
                    "WITH e, t, COUNT { (e)-[:CITES]->() } AS position "
                    "CREATE (e)-[c:CITES]->(t) SET c = $edge, c.position = position",
                    entity=row["entity_id"], unit=row["text_unit_id"], edge=edge,
                ).consume()
            for relationship in relationships:
                _write(tx, "Relationship", *_relationship_node(relationship))

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(write)
        except ConstraintError as error:
            raise DuplicateRecordError(f"Extraction {key} already exists") from error

    def create_relationship(self, relationship: Relationship) -> None:
        self._create("Relationship", *_relationship_node(relationship))

    def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        found = self._get("Relationship", relationship_id)
        if found is None:
            return None
        return _load(Relationship, found[0], ("properties",), {"provenance": found[1]})

    def create_claim(self, claim: Claim) -> None:
        # Verification happens only through Neo4jClaimStore.review (ADR-0002).
        if claim.status == "verified" or claim.verified_at or claim.verified_by:
            raise ValueError("Claims cannot be stored as verified without review")
        self._create("Claim", *claim_node(claim))

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
