"""Write the committed API contract: python scripts/openapi.py

Run after an intended API change; tests/test_openapi.py fails until you do.
"""

import json
from pathlib import Path

from aether.api.app import Settings, create_app

CONTRACT = Path(__file__).resolve().parents[1] / "docs/openapi.json"
OFFLINE = Settings(neo4j_uri="bolt://127.0.0.1:1", neo4j_username="-",
                   neo4j_password="-", neo4j_database="-")


def contract() -> str:
    return json.dumps(create_app(OFFLINE).openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT.write_text(contract())
    print(f"wrote {CONTRACT}")
