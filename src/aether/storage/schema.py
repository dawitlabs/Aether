"""Version 1: record identity and document lookup. Version 2: extraction.
Version 3: communities.
Version 4: contributors."""

from neo4j import Driver


SCHEMA_VERSION = 4
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
V2_STATEMENTS = (
    "CREATE CONSTRAINT aether_v2_extraction_key IF NOT EXISTS "
    "FOR (x:Extraction) REQUIRE x.key IS UNIQUE",
    "CREATE INDEX aether_v2_entity_name_key IF NOT EXISTS "
    "FOR (e:Entity) ON (e.name_key, e.type)",
)
V3_STATEMENTS = (
    "CREATE CONSTRAINT aether_v3_community_id IF NOT EXISTS "
    "FOR (c:Community) REQUIRE c.id IS UNIQUE",
)
V4_STATEMENTS = (
    "CREATE CONSTRAINT aether_v4_contributor_id IF NOT EXISTS "
    "FOR (c:Contributor) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT aether_v4_contributor_key IF NOT EXISTS "
    "FOR (c:Contributor) REQUIRE c.key_hash IS UNIQUE",
)

STATEMENTS = (*V1_STATEMENTS, *V2_STATEMENTS, *V3_STATEMENTS, *V4_STATEMENTS)


def ensure_schema(driver: Driver, database: str) -> None:
    """Apply additive schema statements; safe to repeat after interruption.

    Run explicitly at application setup, before constructing repositories.
    Versions are additive statement lists, not a general migration runner.
    """
    # Auto-commit without retries, so an unreachable database fails fast.
    with driver.session(database=database) as session:
        for statement in STATEMENTS:
            session.run(statement).consume()
