import re
import time
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.core.models import Entity, ProvenanceRef, Relationship, TextUnit
from aether.extraction.pipeline import Extractor
from aether.storage.vectors import LanceVectorIndex


class ReportChat:
    """Cites the first supplied passage and one invented ID."""

    model = "fake-chat"

    def chat_json(self, system, user):
        first = re.search(r'<passage id="([^"]+)">', user).group(1)
        return {"title": "Cluster", "summary": "Linked concepts.", "findings": [
            {"text": "They are linked.", "text_unit_ids": [first, str(uuid4())]},
        ]}


class FixedEmbedder:
    model = "fake-embed"

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
def clusters(knowledge, store, document_ids):
    """Two triangles of entities joined by one weak relationship."""
    graph, created = knowledge
    text = f"cluster test {uuid4()}"
    unit = store.create(TextUnit(
        text=text, source_document_id=document_ids[0], token_count=3,
        media_type="text/plain", content_hash=sha256(text.encode()).hexdigest(),
    ))
    ref = ProvenanceRef(text_unit_id=unit.id, extracted_by="test")
    entities = [
        Entity(name=f"n{i}", type="concept", description="", confidence=1, provenance=[ref])
        for i in range(6)
    ]
    for entity in entities:
        graph.create_entity(entity)
    pairs = [(0, 1, 1.0), (1, 2, 1.0), (0, 2, 1.0), (3, 4, 1.0), (4, 5, 1.0), (3, 5, 1.0),
             (2, 3, 0.05)]
    for a, b, weight in pairs:
        rel = Relationship(source_id=entities[a].id, target_id=entities[b].id, type="LINKED",
                           description="", weight=weight, provenance=[ref])
        graph.create_relationship(rel)
        created.append(rel.id)
    created.extend(e.id for e in entities)
    return [e.id for e in entities]


def test_rebuild_groups_clusters_and_writes_grounded_reports(clusters, database, tmp_path, auth):
    settings = Settings.from_env().model_copy(update={"index_dir": tmp_path})
    with TestClient(create_app(settings), headers=auth) as client:
        client.app.state.extractor = Extractor(ReportChat(), FixedEmbedder(), None, tmp_path)
        assert client.get("/communities/rebuild").json() == {"status": "not_started"}
        assert client.post("/communities/rebuild").status_code == 202
        for _ in range(100):
            status = client.get("/communities/rebuild").json()["status"]
            if status != "running":
                break
            time.sleep(0.05)
        assert status == "complete"
        communities = client.get("/communities").json()

    membership = {
        entity_id: community["id"]
        for community in communities
        for entity_id in community["entity_ids"]
    }
    ids = [str(i) for i in clusters]
    assert membership[ids[0]] == membership[ids[1]] == membership[ids[2]]
    assert membership[ids[3]] == membership[ids[4]] == membership[ids[5]]
    assert membership[ids[0]] != membership[ids[3]]

    ours = [c for c in communities if c["id"] in {membership[ids[0]], membership[ids[3]]}]
    unit_id = clusters_unit_id(database, clusters[0])
    for community in ours:
        assert community["title"] == "Cluster"
        assert community["findings"] == [
            {"text": "They are linked.", "text_unit_ids": [unit_id]}
        ]
    index = LanceVectorIndex(tmp_path, model="fake-embed", dimensions=3)
    indexed = {str(m.source_id) for m in index.search([1.0, 0.0, 0.0], limit=100, kind="community")}
    assert {c["id"] for c in ours} <= indexed


def clusters_unit_id(database, entity_id):
    driver, name = database
    records, _, _ = driver.execute_query(
        "MATCH (:Entity {id: $id})-[:CITES]->(t:TextUnit) RETURN t.id AS id",
        parameters_={"id": str(entity_id)}, database_=name,
    )
    return records[0]["id"]
