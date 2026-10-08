from hashlib import sha256
from uuid import uuid4

import pytest

from aether.core.models import (
    Claim,
    ContributorRef,
    Entity,
    EvidenceRef,
    ProvenanceRef,
    Relationship,
    TextUnit,
)
from aether.storage.knowledge import DuplicateRecordError, MissingReferenceError


@pytest.fixture
def units(store, document_ids):
    created = []
    for index, text in enumerate(["Nix builds shells.", "ሰላም Nix."]):
        unit = TextUnit(
            text=text,
            source_document_id=document_ids[index],
            token_count=4,
            media_type="text/plain",
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
        )
        created.append(store.create(unit))
    return created


def provenance(unit, excerpt=None):
    return ProvenanceRef(
        text_unit_id=unit.id, excerpt=excerpt, relevance=0.7, extracted_by="manual-review"
    )


def evidence(unit, supports):
    return EvidenceRef(text_unit_id=unit.id, supports=supports, added_by="manual-review")


def make_entity(knowledge, units, **overrides):
    store, created = knowledge
    entity = Entity(
        **{
            "name": "Nix",
            "type": "software",
            "description": "A package manager.",
            "confidence": 0.9,
            "provenance": [provenance(units[1], "ሰላም"), provenance(units[0])],
        }
        | overrides
    )
    created.append(entity.id)
    return entity


def stored_entities(knowledge, units, count=2):
    entities = [make_entity(knowledge, units) for _ in range(count)]
    for entity in entities:
        knowledge[0].create_entity(entity)
    return entities


def make_relationship(knowledge, source, target, unit, **overrides):
    relationship = Relationship(
        **{
            "source_id": source.id,
            "target_id": target.id,
            "type": "DEPENDS_ON",
            "description": "Source depends on target.",
            "weight": 0.5,
            "provenance": [provenance(unit)],
        }
        | overrides
    )
    knowledge[1].append(relationship.id)
    return relationship


def make_claim(knowledge, units, **overrides):
    claim = Claim(
        **{
            "statement": "Nix provides development shells.",
            "confidence": 0.8,
            "polarity": "mixed",
            "evidence": [evidence(units[0], True)],
            "counter_evidence": [evidence(units[1], False)],
        }
        | overrides
    )
    knowledge[1].append(claim.id)
    return claim


def test_entity_round_trip_preserves_provenance_order(knowledge, units):
    entity = make_entity(
        knowledge,
        units,
        aliases=["nix", "Nix package manager"],
        properties={"site": "nixos.org", "nested": {"tags": [1, None, True]}},
        embedding=[0.1, -0.2],
    )
    knowledge[0].create_entity(entity)
    assert knowledge[0].get_entity(entity.id) == entity


def test_entities_may_share_a_name(knowledge, units):
    first, second = stored_entities(knowledge, units)
    assert first.name == second.name
    assert knowledge[0].get_entity(second.id) == second


def test_relationships_between_same_entities_stay_distinct(knowledge, units, database):
    source, target = stored_entities(knowledge, units)
    first = make_relationship(knowledge, source, target, units[0])
    second = make_relationship(knowledge, source, target, units[1], type="MENTIONS")
    knowledge[0].create_relationship(first)
    knowledge[0].create_relationship(second)
    assert knowledge[0].get_relationship(first.id) == first
    assert knowledge[0].get_relationship(second.id) == second

    driver, name = database
    records, _, _ = driver.execute_query(
        "MATCH (s:Entity)<-[:FROM]-(r:Relationship)-[:TO]->(t:Entity) "
        "WHERE r.id IN $ids RETURN s.id AS source, t.id AS target",
        parameters_={"ids": [str(first.id), str(second.id)]}, database_=name,
    )
    assert {(r["source"], r["target"]) for r in records} == {(str(source.id), str(target.id))}
    assert len(records) == 2


def test_claim_round_trip_splits_evidence(knowledge, units):
    subject, obj = stored_entities(knowledge, units)
    older = make_claim(knowledge, units)
    knowledge[0].create_claim(older)
    claim = make_claim(
        knowledge,
        units,
        subject_id=subject.id,
        predicate="PROVIDES",
        object_id=obj.id,
        supersedes_id=older.id,
        contributors=[ContributorRef(contributor_id=uuid4())],
    )
    knowledge[0].create_claim(claim)
    assert knowledge[0].get_claim(claim.id) == claim


def test_missing_text_unit_rolls_back_entire_write(knowledge, units, database):
    missing = TextUnit.model_construct(id=uuid4())
    entity = make_entity(knowledge, units, provenance=[provenance(units[0]), provenance(missing)])
    with pytest.raises(MissingReferenceError, match=str(missing.id)):
        knowledge[0].create_entity(entity)
    assert knowledge[0].get_entity(entity.id) is None

    driver, name = database
    records, _, _ = driver.execute_query(
        "MATCH (:TextUnit {id: $id})<-[c:CITES]-() RETURN count(c) AS cites",
        parameters_={"id": str(units[0].id)}, database_=name,
    )
    assert records[0]["cites"] == 0


def test_missing_entity_rejects_relationship(knowledge, units):
    (source,) = stored_entities(knowledge, units, count=1)
    absent = make_entity(knowledge, units)
    relationship = make_relationship(knowledge, source, absent, units[0])
    with pytest.raises(MissingReferenceError, match=str(absent.id)):
        knowledge[0].create_relationship(relationship)
    assert knowledge[0].get_relationship(relationship.id) is None


@pytest.mark.parametrize("field", ["subject_id", "supersedes_id"])
def test_missing_claim_link_rejects_claim(knowledge, units, field):
    claim = make_claim(knowledge, units, **{field: uuid4()})
    with pytest.raises(MissingReferenceError):
        knowledge[0].create_claim(claim)
    assert knowledge[0].get_claim(claim.id) is None


def test_duplicate_id_does_not_overwrite_or_add_citations(knowledge, units):
    (entity,) = stored_entities(knowledge, units, count=1)
    replacement = entity.model_copy(update={"name": "Changed"})
    with pytest.raises(DuplicateRecordError):
        knowledge[0].create_entity(replacement)
    assert knowledge[0].get_entity(entity.id) == entity


@pytest.mark.parametrize(
    "overrides",
    [{"status": "verified"}, {"verified_by": "someone"}],
)
def test_claim_cannot_be_stored_as_verified(knowledge, units, overrides):
    claim = make_claim(knowledge, units, **overrides)
    with pytest.raises(ValueError, match="without review"):
        knowledge[0].create_claim(claim)
    assert knowledge[0].get_claim(claim.id) is None
