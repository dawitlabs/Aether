"""Contributor identities and API keys.

Keys are 32 random bytes, shown once at creation, and stored only as SHA-256
hashes. A fast hash is enough: the keys are high-entropy, not passwords.
Revoking a key clears its hash; the contributor stays for attribution.
"""

import secrets
from hashlib import sha256
from uuid import UUID

from neo4j import Driver

from aether.core.models import Contributor

KEY_PREFIX = "ae_"


def hash_key(key: str) -> str:
    return sha256(key.encode()).hexdigest()


class Neo4jContributorStore:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def create(self, contributor: Contributor) -> str:
        """Store the contributor and return its new API key."""
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        self._driver.execute_query(
            "CREATE (c:Contributor) SET c = $props",
            parameters_={"props": {
                **contributor.model_dump(mode="json"), "key_hash": hash_key(key),
            }},
            database_=self._database,
        )
        return key

    def _find(self, query: str, **params: object) -> Contributor | None:
        records, _, _ = self._driver.execute_query(
            query + " RETURN c {.id, .type, .display_name, .permissions, .created_at} AS c",
            parameters_=params, database_=self._database, routing_="r",
        )
        return Contributor.model_validate(records[0]["c"]) if records else None

    def authenticate(self, key: str) -> Contributor | None:
        if not key.startswith(KEY_PREFIX):
            return None
        return self._find("MATCH (c:Contributor {key_hash: $hash})", hash=hash_key(key))

    def get(self, contributor_id: UUID) -> Contributor | None:
        return self._find("MATCH (c:Contributor {id: $id})", id=str(contributor_id))

    def revoke_key(self, contributor_id: UUID) -> bool:
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Contributor {id: $id}) REMOVE c.key_hash RETURN count(c) AS n",
            parameters_={"id": str(contributor_id)}, database_=self._database,
        )
        return records[0]["n"] == 1
