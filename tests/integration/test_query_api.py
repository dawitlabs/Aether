import re
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.extraction.pipeline import Extractor
from aether.storage.knowledge import Neo4jKnowledgeStore

API = "http://testserver/api/v0"

PLAIN = {"content-type": "text/plain; charset=utf-8"}


class FakeChat:
    """Extracts Bob WORKS_FOR Acme; reports on any community; answers citing every
    passage, plus an unknown ID, a fabricated quote, and junk."""

    model = "fake-chat"

    def __init__(self, bob, acme):
        self.bob, self.acme = bob, acme

    def chat_json(self, system, user):
        ids = re.findall(r'<passage id="([^"]+)">', user)
        if system.startswith("Answer"):
            quote = f"works for {self.acme}"
            return {"answer": f"{self.bob} works for {self.acme}.", "citations": [
                *({"text_unit_id": i, "quote": quote.upper()} for i in ids),
                {"text_unit_id": str(uuid4()), "quote": quote},
                {"text_unit_id": ids[0], "quote": "founded Acme"},
                ids[0],
            ]}
        if system.startswith("You summarize"):
            match = re.search(r'<passage id="[^"]+">\n(.*?)\n</passage>', user, re.S)
            first = match.group(1) if match else ""
            return {"title": "Employment", "summary": f"About: {first}",
                    "findings": [{"text": "Employment.", "text_unit_ids": ids[:1]}]}
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
def graph(database, tmp_path, auth):
    driver, name = database
    tag = uuid4().hex[:8]
    bob, acme = f"Bob{tag}", f"Acme{tag}"
    settings = Settings.from_env().model_copy(
        update={"documents_dir": tmp_path / "docs", "index_dir": tmp_path / "index"}
    )
    with TestClient(create_app(settings), base_url=API, headers=auth) as client:
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
        "OPTIONAL MATCH (t)<-[:CITES|OF]-(n) "
        "OPTIONAL MATCH (r:Review)-[:REVIEWS]->(n) DETACH DELETE r, n",
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
    assert [(c["text_unit_id"], c["quote"]) for c in body["citations"]] == [
        (hood_unit, f"WORKS FOR {acme.upper()}")
    ]
    assert body["citations"][0]["document_id"] == graph_document(client, hood_unit)
    assert len(body["entity_ids"]) >= 1
    assert body["community_ids"] == []


def graph_document(client, unit_id):
    entity = client.get("/entities", params={"name": "bob"}).json()
    for e in entity:
        for unit in client.get(f"/entities/{e['id']}/neighborhood").json()["text_units"]:
            if unit["id"] == unit_id:
                return unit["source_document_id"]
    raise AssertionError("unit not found")


def test_global_mode_answers_from_community_reports(graph):
    client, bob, acme = graph
    client.post("/communities/rebuild")
    for _ in range(200):
        if client.get("/communities/rebuild").json()["status"] != "running":
            break
        time.sleep(0.05)
    assert client.get("/communities/rebuild").json()["status"] == "complete"

    # The dev database may hold other communities; only ours mentions Bob.
    question = f"What themes involve {bob}?"
    body = client.post("/query", json={"question": question, "mode": "global"}).json()

    assert body["entity_ids"] == []
    assert len(body["community_ids"]) >= 1
    assert [c["quote"] for c in body["citations"]] == [f"WORKS FOR {acme.upper()}"]


def test_query_without_matching_entities_skips_the_model(database, tmp_path):
    settings = Settings.from_env().model_copy(update={"index_dir": tmp_path / "empty"})
    with TestClient(create_app(settings), base_url=API) as client:
        client.app.state.extractor = Extractor(
            FakeChat("x", "y"), NameEmbedder([]), None, tmp_path / "empty"
        )
        body = client.post("/query", json={"question": "Anything?"}).json()
    assert body == {
        "answer": None, "citations": [], "entity_ids": [], "community_ids": [], "claim_ids": [],
    }


def test_local_query_includes_verified_claims_about_matched_entities(graph, make_contributor):
    client, bob, acme = graph
    bob_entity = client.get("/entities", params={"name": bob}).json()[0]
    unit = client.get(f"/entities/{bob_entity['id']}/neighborhood").json()["text_units"][0]
    claim = client.post("/claims", json={
        "statement": f"{bob} is employed by {acme}.", "subject_id": bob_entity["id"],
        "confidence": 0.9, "evidence": [{"text_unit_id": unit["id"], "excerpt": "works for"}],
    }).json()["claim"]["id"]
    question = {"question": f"Who employs {bob}?"}
    assert claim not in client.post("/query", json=question).json()["claim_ids"]

    _, reviewer_key = make_contributor(permissions=["review"])
    client.post(f"/claims/{claim}/review", json={"decision": "accept"},
                headers={"Authorization": f"Bearer {reviewer_key}"})

    assert claim in client.post("/query", json=question).json()["claim_ids"]
