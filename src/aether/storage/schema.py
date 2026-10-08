"""Version 1: text-unit identity and document lookup schema."""

from neo4j import Driver


SCHEMA_VERSION = 1
V1_STATEMENTS = (
    "CREATE CONSTRAINT aether_v1_text_unit_id IF NOT EXISTS "
    "FOR (t:TextUnit) REQUIRE t.id IS UNIQUE",
    "CREATE INDEX aether_v1_text_unit_document IF NOT EXISTS "
    "FOR (t:TextUnit) ON (t.source_document_id)",
)


def ensure_schema(driver: Driver, database: str) -> None:
    """Apply additive schema statements; safe to repeat after interruption.

    Run explicitly at application setup, before constructing repositories.
    This is the first schema version, not a general migration runner.
    """
    for statement in V1_STATEMENTS:
        driver.execute_query(statement, database_=database)
