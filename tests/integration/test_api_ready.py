from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app


def test_ready_succeeds_against_local_neo4j(database):
    # The database fixture skips unless AETHER_TEST_NEO4J=1 and Neo4j is local.
    with TestClient(create_app(Settings.from_env())) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
