"""Version 1: record identity and document lookup schema."""

from neo4j import Driver


SCHEMA_VERSION = 1
V1_STATEMENTS = (
    "CREATE CONSTRAINT aether_v1_text_unit_id IF NOT EXISTS "
    "FOR (t:TextUnit) REQUIRE t.id IS UNIQUE",
    "CREATE INDEX aether_v1_text_unit_document IF NOT EXISTS "
    "FOR (t:TextUnit) ON (t.source_document_id)",
    "CREATE CONSTRAINT aether_v1_entity_id IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT aether_v1_relationship_id IF NOT EXISTS "
    "FOR (r:Relationship) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT aether_v1_claim_id IF NOT EXISTS "
    "FOR (c:Claim) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT aether_v1_document_id IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT aether_v1_document_hash IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.content_hash IS UNIQUE",
)


def ensure_schema(driver: Driver, database: str) -> None:
    """Apply additive schema statements; safe to repeat after interruption.

    Run explicitly at application setup, before constructing repositories.
    This is the first schema version, not a general migration runner.
    """
    # Auto-commit without retries, so an unreachable database fails fast.
    with driver.session(database=database) as session:
        for statement in V1_STATEMENTS:
            session.run(statement).consume()
