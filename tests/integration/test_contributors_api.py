from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.extraction.pipeline import Extractor
from aether.storage.contributors import Neo4jContributorStore

API = "http://testserver/api/v0"


@pytest.fixture
def client(database):
    with TestClient(create_app(Settings.from_env()), base_url=API) as test_client:
        yield test_client


class FixedEmbedder:
    model = "fake-embed"

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"},
    {"Authorization": "Bearer ae_not-a-real-key"}, {"Authorization": "Bearer wrong-prefix"},
])
def test_writes_reject_missing_or_invalid_keys(client, headers):
    response = client.post("/communities/rebuild", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_admin_creates_agent_whose_key_works_once_stored_hashed(client, auth, database):
    response = client.post("/contributors", headers=auth,
                           json={"type": "agent", "display_name": "bot"})
    assert response.status_code == 201
    created = response.json()
    agent_id, key = created["contributor"]["id"], created["api_key"]
    try:
        me = client.get("/contributors/me", headers=bearer(key)).json()
        assert (me["id"], me["permissions"]) == (agent_id, ["propose"])
        driver, name = database
        records, _, _ = driver.execute_query(
            "MATCH (c:Contributor {id: $id}) RETURN properties(c) AS p",
            parameters_={"id": agent_id}, database_=name,
        )
        stored = records[0]["p"]
        assert stored["key_hash"] == sha256(key.encode()).hexdigest()
        assert key not in stored.values()
    finally:
        driver, name = database
        driver.execute_query("MATCH (c:Contributor {id: $id}) DELETE c",
                             parameters_={"id": agent_id}, database_=name)


def test_agents_cannot_be_granted_review(client, auth):
    response = client.post("/contributors", headers=auth, json={
        "type": "agent", "display_name": "bot", "permissions": ["propose", "review"],
    })
    assert response.status_code == 422


def test_non_admin_cannot_create_contributors(client, make_contributor):
    _, key = make_contributor(permissions=["propose", "review"])
    response = client.post("/contributors", headers=bearer(key),
                           json={"type": "human", "display_name": "x"})
    assert response.status_code == 403


def test_reviewer_without_propose_cannot_write(client, make_contributor):
    _, key = make_contributor(permissions=["review"])
    assert client.post("/communities/rebuild", headers=bearer(key)).status_code == 403


def test_revoked_key_is_rejected(client, make_contributor, database):
    contributor, key = make_contributor()
    assert client.get("/contributors/me", headers=bearer(key)).status_code == 200
    driver, name = database
    assert Neo4jContributorStore(driver, name).revoke_key(contributor.id)
    assert client.get("/contributors/me", headers=bearer(key)).status_code == 401


@pytest.fixture
def strict(database, tmp_path):
    settings = Settings.from_env().model_copy(update={
        "write_limit_per_minute": 2, "query_limit_per_minute": 2, "index_dir": tmp_path,
    })
    with TestClient(create_app(settings), base_url=API) as test_client:
        # A fake embedder over an empty index: queries answer without any model call.
        test_client.app.state.extractor = Extractor(None, FixedEmbedder(), None, tmp_path)
        yield test_client


def test_writes_are_rate_limited_per_contributor(strict, make_contributor):
    _, key = make_contributor()
    _, other = make_contributor()
    # Auth and limits run before the body is read, so a rejected upload still counts.
    wrong_type = {"content-type": "application/json"}
    statuses = [strict.post("/documents", content=b"{}", headers={**wrong_type, **bearer(key)})
                .status_code for _ in range(3)]
    assert statuses == [415, 415, 429]
    blocked = strict.post("/documents", content=b"{}", headers={**wrong_type, **bearer(key)})
    assert blocked.json()["error"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert strict.post("/documents", content=b"{}",
                       headers={**wrong_type, **bearer(other)}).status_code == 415


def test_queries_are_limited_per_ip_without_a_key(strict, make_contributor):
    question = {"question": "anything"}
    assert [strict.post("/query", json=question).status_code for _ in range(3)] == [
        200, 200, 429]
    _, key = make_contributor()
    assert strict.post("/query", json=question, headers=bearer(key)).status_code == 200
    assert strict.post("/query", json=question,
                       headers=bearer("ae_invalid")).status_code == 401


def test_rotated_key_replaces_the_old_one(client, make_contributor):
    _, old = make_contributor()
    new = client.post("/contributors/me/key", headers=bearer(old)).json()["api_key"]
    assert client.get("/contributors/me", headers=bearer(old)).status_code == 401
    assert client.get("/contributors/me", headers=bearer(new)).status_code == 200


def test_admin_revokes_keys_over_the_api(client, auth, make_contributor):
    target, key = make_contributor()
    _, plain = make_contributor()
    assert client.post(f"/contributors/{target.id}/revoke",
                       headers=bearer(plain)).status_code == 403
    assert client.post(f"/contributors/{target.id}/revoke", headers=auth).status_code == 204
    assert client.get("/contributors/me", headers=bearer(key)).status_code == 401
