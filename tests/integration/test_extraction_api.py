import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.extraction.llm import LLMError
from aether.extraction.pipeline import Extractor
from aether.storage.knowledge import Neo4jKnowledgeStore

API = "http://testserver/api/v0"

PLAIN = {"content-type": "text/plain; charset=utf-8"}


class FlakyChat:
    """Extracts one entity per unit; fails once on the unit containing `fail_on`."""

    model = "fake-chat"

    def __init__(self, fail_on):
        self.fail_on = fail_on

    def chat_json(self, system, user):
        if self.fail_on and self.fail_on in user:
            self.fail_on = None
            raise LLMError("Provider returned HTTP 429")
        name = user.split()[1]
        return {"entities": [{"name": name, "type": "concept", "description": "",
                              "excerpt": name, "confidence": 1}]}


class OneHotEmbedder:
    model = "fake-embed"

    def embed(self, texts):
        return [[1.0 if i == hash(t) % 64 else 0.0 for i in range(64)] for t in texts]


@pytest.fixture
def client(database, tmp_path, auth):
    driver, name = database
    settings = Settings.from_env().model_copy(
        update={"documents_dir": tmp_path / "docs", "index_dir": tmp_path / "index"}
    )
    uploaded = []
    with TestClient(create_app(settings), base_url=API, headers=auth) as test_client:
        yield test_client, uploaded
    driver.execute_query(
        "MATCH (t:TextUnit) WHERE t.source_document_id IN $ids "
        "OPTIONAL MATCH (t)<-[:CITES|OF]-(n) DETACH DELETE n",
        parameters_={"ids": uploaded}, database_=name,
    )
    driver.execute_query(
        "MATCH (n) WHERE (n:Document AND n.id IN $ids) "
        "OR (n:TextUnit AND n.source_document_id IN $ids) DELETE n",
        parameters_={"ids": uploaded}, database_=name,
    )


def wait(test_client, path):
    for _ in range(100):
        body = test_client.get(path).json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


def test_failed_job_resumes_without_duplicates(client, database, tmp_path):
    test_client, uploaded = client
    tag = uuid4().hex[:8]
    # Three units: each paragraph is at the 2,000-character split limit.
    paragraphs = [f"Alpha{tag} " + "x" * 1980, f"Beta{tag} " + "y" * 1980, f"Gamma{tag} " + "z"]
    response = test_client.post("/documents", content="\n\n".join(paragraphs), headers=PLAIN)
    document_id = response.json()["id"]
    uploaded.append(document_id)
    driver, name = database
    test_client.app.state.extractor = Extractor(
        FlakyChat(fail_on=f"Beta{tag}"), OneHotEmbedder(),
        Neo4jKnowledgeStore(driver, name), tmp_path / "index",
    )
    path = f"/documents/{document_id}/extraction"

    assert test_client.get(path).json() == {"status": "not_started", "extracted": 0, "total": 3}
    assert test_client.post(path).status_code == 202
    assert wait(test_client, path) == {"status": "failed", "extracted": 1, "total": 3}

    test_client.post(path)
    assert wait(test_client, path) == {"status": "complete", "extracted": 3, "total": 3}
    records, _, _ = driver.execute_query(
        "MATCH (e:Entity) WHERE e.name IN $names RETURN e.name AS name, "
        "COUNT { (e)-[:CITES]->() } AS cites ORDER BY name",
        parameters_={"names": [p.split()[0] for p in paragraphs]}, database_=name,
    )
    assert [(r["name"], r["cites"]) for r in records] == [
        (f"Alpha{tag}", 1), (f"Beta{tag}", 1), (f"Gamma{tag}", 1)
    ]


def test_unknown_document_is_404(client):
    test_client, _ = client
    assert test_client.post(f"/documents/{uuid4()}/extraction").status_code == 404
    assert test_client.get(f"/documents/{uuid4()}/extraction").status_code == 404
