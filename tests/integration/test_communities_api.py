import time
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.core.models import Entity, ProvenanceRef, Relationship, TextUnit


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


def test_rebuild_groups_dense_clusters(clusters, database):
    with TestClient(create_app(Settings.from_env())) as client:
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
