"""Neo4j create/read operations for text units; caller owns the driver."""

import json
from collections.abc import Mapping
from uuid import UUID

from neo4j import Driver
from neo4j.exceptions import ConstraintError

from aether.core.models import TextUnit


class DuplicateTextUnitError(ValueError):
    """A text unit with the supplied ID already exists."""


def _properties(unit: TextUnit) -> dict[str, object]:
    # Revalidate even if a caller mutated a previously valid model.
    validated = TextUnit.model_validate(unit.model_dump())
    properties = validated.model_dump(mode="json", exclude={"metadata"})
    properties["metadata_json"] = json.dumps(
        validated.metadata, ensure_ascii=False, allow_nan=False
    )
    return properties


def _model(properties: Mapping[str, object]) -> TextUnit:
    data = dict(properties)
    metadata = data.pop("metadata_json")
    if not isinstance(metadata, str):
        raise ValueError("Stored text-unit metadata must be JSON text")
    data["metadata"] = json.loads(metadata)
    return TextUnit.model_validate(data)


class Neo4jTextUnitStore:
    """Use after ensure_schema(); does not open or close the shared driver."""

    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def create(self, unit: TextUnit) -> TextUnit:
        properties = _properties(unit)
        try:
            records, _, _ = self._driver.execute_query(
                "CREATE (t:TextUnit) SET t = $properties RETURN properties(t) AS data",
                parameters_={"properties": properties},
                database_=self._database,
            )
        except ConstraintError as error:
            raise DuplicateTextUnitError(f"Text unit {unit.id} already exists") from error
        return _model(records[0]["data"])

    def get(self, unit_id: UUID) -> TextUnit | None:
        records, _, _ = self._driver.execute_query(
            "MATCH (t:TextUnit {id: $id}) RETURN properties(t) AS data",
            parameters_={"id": str(unit_id)},
            database_=self._database,
            routing_="r",
        )
        return _model(records[0]["data"]) if records else None

    def list_by_document(
        self, document_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[TextUnit]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        records, _, _ = self._driver.execute_query(
            "MATCH (t:TextUnit {source_document_id: $document_id}) "
            "RETURN properties(t) AS data "
            "ORDER BY t.start_offset, t.id SKIP $offset LIMIT $limit",
            parameters_={"document_id": str(document_id), "limit": limit, "offset": offset},
            database_=self._database,
            routing_="r",
        )
        return [_model(record["data"]) for record in records]
