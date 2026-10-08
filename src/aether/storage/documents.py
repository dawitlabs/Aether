"""Neo4j storage for documents; a document and its text units are written together."""

from uuid import UUID

from neo4j import Driver, ManagedTransaction
from neo4j.exceptions import ConstraintError

from aether.core.models import Document, TextUnit
from aether.storage.text_units import text_unit_properties


class DuplicateDocumentError(ValueError):
    """A document with the same ID or content hash already exists."""


class Neo4jDocumentStore:
    """Use after ensure_schema(); does not open or close the shared driver."""

    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def create(self, document: Document, units: list[TextUnit]) -> None:
        if any(unit.source_document_id != document.id for unit in units):
            raise ValueError("every text unit must belong to the document")
        if len(units) != document.text_unit_count:
            raise ValueError("text_unit_count must match the number of text units")
        node = Document.model_validate(document.model_dump()).model_dump(mode="json")
        rows = [text_unit_properties(unit) for unit in units]

        def write(tx: ManagedTransaction) -> None:
            tx.run("CREATE (d:Document) SET d = $node", node=node).consume()
            tx.run("UNWIND $rows AS row CREATE (t:TextUnit) SET t = row", rows=rows).consume()

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(write)
        except ConstraintError as error:
            raise DuplicateDocumentError(f"Document {document.id} already exists") from error

    def _find(self, field: str, value: str) -> Document | None:
        # field is one of two fixed property names, never caller input.
        records, _, _ = self._driver.execute_query(
            f"MATCH (d:Document {{{field}: $value}}) RETURN properties(d) AS data",
            parameters_={"value": value},
            database_=self._database,
            routing_="r",
        )
        return Document.model_validate(records[0]["data"]) if records else None

    def get(self, document_id: UUID) -> Document | None:
        return self._find("id", str(document_id))

    def get_by_hash(self, content_hash: str) -> Document | None:
        return self._find("content_hash", content_hash)
