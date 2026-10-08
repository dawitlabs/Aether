from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import TextUnit
from aether.storage.schema import ensure_schema
from aether.storage.text_units import DuplicateTextUnitError


def make_unit(document_id, **overrides):
    text = "Nix — ሰላም ' MATCH (n) DELETE n //"
    data = {
        "text": text,
        "source_document_id": document_id,
        "token_count": 12,
        "media_type": "text/plain",
        "content_hash": sha256(text.encode("utf-8")).hexdigest(),
    }
    return TextUnit(**(data | overrides))


@pytest.mark.parametrize("full", [False, True])
def test_complete_round_trip(store, document_ids, full):
    extra = {
        "source_uri": "https://example.com/nix",
        "start_offset": 5,
        "end_offset": 40,
        "metadata": {"page": 2, "nested": {"tags": ["nix", None, True]}},
        "embedding": [0.1, -0.2, 0.3],
    } if full else {}
    unit = make_unit(document_ids[0], **extra)
    assert store.create(unit) == unit
    assert store.get(unit.id) == unit


def test_schema_can_be_reapplied_without_data_loss(store, database, document_ids):
    unit = make_unit(document_ids[0])
    store.create(unit)
    ensure_schema(*database)
    ensure_schema(*database)
    assert store.get(unit.id) == unit


def test_duplicate_id_does_not_overwrite_record(store, document_ids):
    original = make_unit(document_ids[0])
    store.create(original)
    replacement = original.model_copy(update={"metadata": {"changed": True}})
    with pytest.raises(DuplicateTextUnitError):
        store.create(replacement)
    assert store.get(original.id) == original


def test_equal_hashes_keep_separate_sources(store, document_ids):
    first = make_unit(document_ids[0])
    second = make_unit(document_ids[1])
    assert first.content_hash == second.content_hash
    store.create(first)
    store.create(second)
    assert store.list_by_document(document_ids[0]) == [first]
    assert store.list_by_document(document_ids[1]) == [second]


def test_document_listing_is_ordered_and_paginated(store, document_ids):
    first = make_unit(document_ids[0], start_offset=0, end_offset=40)
    second = make_unit(document_ids[0], start_offset=40, end_offset=80)
    store.create(second)
    store.create(first)
    assert store.list_by_document(document_ids[0], limit=1) == [first]
    assert store.list_by_document(document_ids[0], limit=1, offset=1) == [second]
    assert store.list_by_document(document_ids[1]) == []


def test_missing_id_returns_none(store):
    assert store.get(uuid4()) is None


def test_mutated_model_is_revalidated_before_write(store, document_ids):
    unit = make_unit(document_ids[0])
    unit.text = "Different content without a matching hash"
    with pytest.raises(ValidationError, match="content_hash must match text"):
        store.create(unit)
    assert store.get(unit.id) is None


def test_corrupted_record_is_rejected_on_read(store, database, document_ids):
    unit = make_unit(document_ids[0])
    store.create(unit)
    driver, name = database
    driver.execute_query(
        "MATCH (t:TextUnit {id: $id}) SET t.content_hash = $hash",
        parameters_={"id": str(unit.id), "hash": "0" * 64}, database_=name,
    )
    with pytest.raises(ValidationError, match="content_hash must match text"):
        store.get(unit.id)
