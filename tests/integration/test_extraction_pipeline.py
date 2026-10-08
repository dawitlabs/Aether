from hashlib import sha256
from uuid import uuid4

import pytest

from aether.core.models import Entity, ProvenanceRef, TextUnit
from aether.extraction.pipeline import Extractor
from aether.storage.knowledge import MissingReferenceError


class FakeChat:
    model = "fake-chat"

    def __init__(self, replies):
        self.replies = replies
        self.calls = 0

    def chat_json(self, system, user):
        self.calls += 1
        return next(reply for text, reply in self.replies if text in user)


class FakeEmbedder:
    """Names in `same` share one vector; every other name gets its own axis."""

    model = "fake-embed"

    def __init__(self, same=()):
        self.same = set(same)
        self.axes = {}

    def embed(self, texts):
        vectors = []
        for text in texts:
            name = text.split(" (")[0]
            axis = 0 if name in self.same else self.axes.setdefault(name, len(self.axes) + 1)
            vectors.append([1.0 if i == axis else 0.0 for i in range(64)])
        return vectors


def ent(name, excerpt=None, type_="organization"):
    return {"name": name, "type": type_, "description": f"{name}.",
            "excerpt": excerpt or name, "confidence": 0.9}


@pytest.fixture
def make_unit(store, document_ids, database):
    driver, name = database

    def make(text):
        unit = TextUnit(
            text=text, source_document_id=document_ids[0], token_count=len(text.split()),
            media_type="text/plain", content_hash=sha256(text.encode()).hexdigest(),
        )
        return store.create(unit)

    yield make
    driver.execute_query(
        "MATCH (t:TextUnit {source_document_id: $doc})<-[:CITES|OF]-(n) DETACH DELETE n",
        parameters_={"doc": str(document_ids[0])}, database_=name,
    )


@pytest.fixture
def tag():
    return uuid4().hex[:8]


def count(database, query, **params):
    driver, name = database
    records, _, _ = driver.execute_query(query, parameters_=params, database_=name)
    return records[0][0]


def test_same_entity_in_two_units_gets_one_node_and_two_citations(
    knowledge, make_unit, database, tmp_path, tag
):
    store, _ = knowledge
    acme, bob = f"Acme{tag}", f"Bob{tag}"
    first = make_unit(f"{acme} builds rockets.")
    second = make_unit(f"{bob} works for {acme}.")
    chat = FakeChat([
        (f"{bob} works", {"entities": [ent(acme), ent(bob, type_="person")], "relationships": [
            {"source": bob, "target": acme, "type": "works for", "description": "Job.",
             "excerpt": f"works for {acme}", "confidence": 0.8}]}),
        (f"{acme} builds", {"entities": [ent(acme)], "relationships": []}),
    ])
    extractor = Extractor(chat, FakeEmbedder(), store, tmp_path)

    assert extractor.run(first) and extractor.run(second)

    acme_id = store.find_entity(acme, "organization")
    entity = store.get_entity(acme_id)
    assert [ref.text_unit_id for ref in entity.provenance] == [first.id, second.id]
    assert entity.provenance[1].extracted_by == "extract-v1/fake-chat"
    assert count(database, "MATCH (e:Entity {name: $n}) RETURN count(e)", n=acme) == 1
    assert count(
        database,
        "MATCH (:Entity {name: $b})<-[:FROM]-(r:Relationship {type: 'WORKS_FOR'})"
        "-[:TO]->(:Entity {name: $a}) RETURN count(r)", a=acme, b=bob,
    ) == 1


def test_embedding_match_requires_same_type(knowledge, make_unit, database, tmp_path, tag):
    store, _ = knowledge
    corp, corporation, place = f"Acme{tag} Corp", f"Acme{tag} Corporation", f"Acme{tag} City"
    first = make_unit(f"{corp} opened.")
    second = make_unit(f"{corporation} grew in {place}.")
    chat = FakeChat([
        (f"{corp} opened", {"entities": [ent(corp)]}),
        (f"{corporation} grew", {"entities": [ent(corporation), ent(place, type_="location")]}),
    ])
    embedder = FakeEmbedder(same={corp, corporation, place})
    extractor = Extractor(chat, embedder, store, tmp_path)

    extractor.run(first)
    extractor.run(second)

    corp_id = store.find_entity(corp, "organization")
    assert len(store.get_entity(corp_id).provenance) == 2
    assert store.find_entity(corporation, "organization") is None
    assert store.find_entity(place, "location") is not None


def test_rerun_skips_extracted_units(knowledge, make_unit, database, tmp_path, tag):
    store, _ = knowledge
    unit = make_unit(f"Acme{tag} exists.")
    chat = FakeChat([(f"Acme{tag}", {"entities": [ent(f"Acme{tag}")]})])
    extractor = Extractor(chat, FakeEmbedder(), store, tmp_path)

    assert extractor.run(unit) is True
    assert extractor.run(unit) is False
    assert chat.calls == 1
    assert count(database, "MATCH (e:Entity {name: $n}) RETURN count(e)", n=f"Acme{tag}") == 1


def test_failed_write_leaves_nothing(knowledge, make_unit, database, tag):
    store, _ = knowledge
    unit = make_unit(f"Acme{tag} exists.")
    ref = ProvenanceRef(text_unit_id=unit.id, extracted_by="test")
    entity = Entity(name=f"Acme{tag}", type="organization", description="",
                    confidence=1, provenance=[ref])

    with pytest.raises(MissingReferenceError):
        store.write_extraction(unit.id, "m", [entity], [(uuid4(), ref)], [])

    assert store.get_entity(entity.id) is None
    assert not store.is_extracted(unit.id, "m")
