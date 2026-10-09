"""Local contributor administration; needs direct database access, not an API key.

    python scripts/contributor.py create-admin "Display Name"
    python scripts/contributor.py revoke <contributor-id>

create-admin prints the new key once. Revoking keeps the contributor for
attribution but makes its key unusable.
"""

import sys
from uuid import UUID

from neo4j import GraphDatabase

from aether.api.app import Settings
from aether.core.models import Contributor
from aether.storage.contributors import Neo4jContributorStore
from aether.storage.schema import ensure_schema


def main(args: list[str]) -> int:
    if len(args) != 2 or args[0] not in ("create-admin", "revoke"):
        print(__doc__, file=sys.stderr)
        return 2
    config = Settings.from_env()
    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_username, config.neo4j_password)
    ) as driver:
        ensure_schema(driver, config.neo4j_database)
        store = Neo4jContributorStore(driver, config.neo4j_database)
        if args[0] == "revoke":
            if not store.revoke_key(UUID(args[1])):
                print("No such contributor", file=sys.stderr)
                return 1
            print("Key revoked")
            return 0
        admin = Contributor(type="human", display_name=args[1],
                            permissions=["propose", "review", "admin"])
        key = store.create(admin)
    print(f"Contributor {admin.id}\nAPI key (shown once): {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
