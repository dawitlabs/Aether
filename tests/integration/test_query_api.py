import re
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.extraction.pipeline import Extractor
from aether.storage.knowledge import Neo4jKnowledgeStore

PLAIN = {"content-type": "text/plain; charset=utf-8"}


class FakeChat:
    """Extracts Bob WORKS_FOR Acme; answers by citing every passage plus a bogus ID."""

    model = "fake-chat"

    def __init__(self, bob, acme):
        self.bob, self.acme = bob, acme

    def chat_json(self, system, user):
        if system.startswith("Answer"):
            ids = re.findall(r'<passage id="([^"]+)">', user)
            return {"answer": f"{self.bob} works for {self.acme}.",
                    "citations": [*ids, str(uuid4()), {"bad": 1}]}
        return {
            "entities": [
                {"name": n, "type": t, "description": "", "excerpt": n, "confidence": 1}
                for n, t in ((self.bob, "person"), (self.acme, "organization"))
            ],
            "relationships": [{"source": self.bob, "target": self.acme, "type": "WORKS_FOR",
                               "description": "", "excerpt": f"works for {self.acme}",
                               "confidence": 0.9}],
        }


class NameEmbedder:
    """Texts mentioning the same known name share a vector."""

    model = "fake-embed"

    def __init__(self, names):
        self.names = names

    def embed(self, texts):
        axis = [next((i for i, n in enumerate(self.names) if n in t), len(self.names))
                for t in texts]
        return [[1.0 if i == a else 0.0 for i in range(8)] for a in axis]


@pytest.fixture
def graph(database, tmp_path):
    driver, name = database
    tag = uuid4().hex[:8]
    bob, acme = f"Bob{tag}", f"Acme{tag}"
    settings = Settings.from_env().model_copy(
        update={"documents_dir": tmp_path / "docs", "index_dir": tmp_path / "index"}
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.extractor = Extractor(
            FakeChat(bob, acme), NameEmbedder([bob, acme]),
            Neo4jKnowledgeStore(driver, name), tmp_path / "index",
        )
        document_id = client.post(
            "/documents", content=f"{bob} works for {acme}.", headers=PLAIN
        ).json()["id"]
        client.post(f"/documents/{document_id}/extraction")
        for _ in range(100):
            if client.get(f"/documents/{document_id}/extraction").json()["status"] != "running":
                break
            time.sleep(0.05)
        yield client, bob, acme
    driver.execute_query(
        "MATCH (t:TextUnit {source_document_id: $id}) "
        "OPTIONAL MATCH (t)<-[:CITES|OF]-(n) DETACH DELETE n",
        parameters_={"id": document_id}, database_=name,
    )
    driver.execute_query(
        "MATCH (n) WHERE (n:Document AND n.id = $id) "
        "OR (n:TextUnit AND n.source_document_id = $id) DELETE n",
        parameters_={"id": document_id}, database_=name,
    )


def test_search_and_neighborhood_trace_to_text(graph):
    client, bob, acme = graph
    found = client.get("/entities", params={"name": bob.lower()}).json()
    assert [e["name"] for e in found] == [bob]
    assert found[0]["embedding"] is None

    hood = client.get(f"/entities/{found[0]['id']}/neighborhood").json()
    assert [e["name"] for e in hood["neighbors"]] == [acme]
    assert [r["type"] for r in hood["relationships"]] == ["WORKS_FOR"]
    assert [u["text"] for u in hood["text_units"]] == [f"{bob} works for {acme}."]
    assert client.get(f"/entities/{uuid4()}/neighborhood").status_code == 404


def test_query_keeps_only_citations_that_were_supplied(graph):
    client, bob, acme = graph
    hood_unit = client.get(
        f"/entities/{client.get('/entities', params={'name': bob}).json()[0]['id']}/neighborhood"
    ).json()["text_units"][0]["id"]

    body = client.post("/query", json={"question": f"Who employs {bob}?"}).json()

    assert body["answer"] == f"{bob} works for {acme}."
    assert body["citations"] == [hood_unit]
    assert len(body["entity_ids"]) >= 1


def test_query_without_matching_entities_skips_the_model(database, tmp_path):
    settings = Settings.from_env().model_copy(update={"index_dir": tmp_path / "empty"})
    with TestClient(create_app(settings)) as client:
        client.app.state.extractor = Extractor(
            FakeChat("x", "y"), NameEmbedder([]), None, tmp_path / "empty"
        )
        body = client.post("/query", json={"question": "Anything?"}).json()
    assert body == {"answer": None, "citations": [], "entity_ids": []}
