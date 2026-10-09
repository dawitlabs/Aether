from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.core.models import TextUnit
from aether.extraction.pipeline import Extractor
from aether.storage.knowledge import Neo4jKnowledgeStore
from aether.storage.merges import Neo4jMergeStore

API = "http://testserver/api/v0"


class Chat:
    model = "fake-chat"

    def __init__(self, replies):
        self.replies = replies

    def chat_json(self, system, user):
        return next(reply for text, reply in self.replies if text in user)


class Embedder:
    """Fixed vectors by entity name: cos(corp, corporation) = 0.9, inside the review band."""

    model = "fake-embed"

    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return [self.vectors[t.split(" (")[0]] for t in texts]


def entity(name, kind="organization"):
    return {"name": name, "type": kind, "description": "", "excerpt": name, "confidence": 1}


@pytest.fixture
def lookalikes(database, store, document_ids, make_contributor, tmp_path):
    driver, name = database
    tag = uuid4().hex[:6]
    corp, corporation, bob = f"Acme{tag} Corp", f"Acme{tag} Corporation", f"Bob{tag}"
    units = []
    for text in (f"{corp} builds rockets.", f"{bob} joined {corporation}."):
        units.append(store.create(TextUnit(
            text=text, source_document_id=document_ids[0], token_count=4,
            media_type="text/plain", content_hash=sha256(text.encode()).hexdigest(),
        )))
    chat = Chat([
        (f"{corp} builds", {"entities": [entity(corp)]}),
        (f"{bob} joined", {"entities": [entity(corporation), entity(bob, "person")],
                           "relationships": [{"source": bob, "target": corporation,
                                              "type": "JOINED", "description": "",
                                              "excerpt": f"joined {corporation}",
                                              "confidence": 0.9}]}),
    ])
    embedder = Embedder({corp: [1.0, 0.0, 0.0], corporation: [0.9, 0.43589, 0.0],
                         bob: [0.0, 0.0, 1.0]})
    extractor = Extractor(chat, embedder, Neo4jKnowledgeStore(driver, name), tmp_path,
                          merges=Neo4jMergeStore(driver, name))
    for unit in units:
        extractor.run(unit)
    _, reviewer = make_contributor(permissions=["review"])
    _, agent = make_contributor(type="agent", display_name="bot")
    with TestClient(create_app(Settings.from_env()), base_url=API) as client:
        yield client, corp, corporation, bob, {
            "reviewer": {"Authorization": f"Bearer {reviewer}"},
            "agent": {"Authorization": f"Bearer {agent}"},
        }
    driver.execute_query(
        "MATCH (t:TextUnit {source_document_id: $doc})<-[:CITES|OF]-(n) "
        "OPTIONAL MATCH (m:MergeCandidate)-[:CANDIDATE|TARGET]->(n) DETACH DELETE m, n",
        parameters_={"doc": str(document_ids[0])}, database_=name,
    )


def candidate_for(client, entity_id):
    open_ = client.get("/merge-candidates", params={"limit": 200}).json()
    return next(c for c in open_ if entity_id in (c["entity_id"], c["target_id"]))


def entity_id(client, name):
    return next(e["id"] for e in client.get("/entities", params={"name": name}).json()
                if e["name"] == name)


def test_lookalike_is_queued_and_a_merge_moves_everything(lookalikes):
    client, corp, corporation, bob, keys = lookalikes
    target, source = entity_id(client, corp), entity_id(client, corporation)
    candidate = candidate_for(client, source)
    assert (candidate["entity_id"], candidate["target_id"]) == (source, target)
    assert candidate["similarity"] == pytest.approx(0.9, abs=0.001)
    review = f"/merge-candidates/{candidate['id']}/review"

    assert client.post(review, json={"decision": "merge"},
                       headers=keys["agent"]).status_code == 403
    merged = client.post(review, json={"decision": "merge"}, headers=keys["reviewer"])
    assert merged.json()["status"] == "merged"
    assert client.post(review, json={"decision": "merge"},
                       headers=keys["reviewer"]).status_code == 409

    assert [e["name"] for e in client.get("/entities", params={"name": corporation}).json()
            if e["name"] == corporation] == []
    hood = client.get(f"/entities/{source}/neighborhood").json()
    assert hood["entity"]["id"] == target
    assert corporation in hood["entity"]["aliases"]
    assert len(hood["entity"]["provenance"]) == 2
    assert [e["name"] for e in hood["neighbors"]] == [bob]
    assert [r["target_id"] for r in hood["relationships"]] == [target]


def test_keep_separate_leaves_both_entities(lookalikes):
    client, corp, corporation, _, keys = lookalikes
    source = entity_id(client, corporation)
    candidate = candidate_for(client, source)
    kept = client.post(f"/merge-candidates/{candidate['id']}/review",
                       json={"decision": "keep_separate"}, headers=keys["reviewer"])
    assert kept.json()["status"] == "kept_separate"
    assert entity_id(client, corporation) == source
    assert entity_id(client, corp) != source
