from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import TextUnit
from aether.storage.schema import ensure_schema
from aether.storage.text_units import (
    DuplicateTextUnitError,
    ImmutableTextUnitError,
    TextUnitNotFoundError,
)


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


def test_update_changes_mutable_fields(store, document_ids):
    unit = store.create(make_unit(document_ids[0]))
    changed = unit.model_copy(
        update={"token_count": 99, "metadata": {"page": 3}, "embedding": [1.0, 2.0]}
    )
    assert store.update(changed) == changed
    assert store.get(unit.id) == changed


@pytest.mark.parametrize(
    "field,value",
    [("source_document_id", uuid4()), ("start_offset", 7), ("created_at", None)],
)
def test_update_rejects_identity_changes(store, document_ids, field, value):
    unit = store.create(make_unit(document_ids[0]))
    if field == "created_at":
        value = unit.created_at.replace(year=2000)
    with pytest.raises(ImmutableTextUnitError, match=field):
        store.update(unit.model_copy(update={field: value}))
    assert store.get(unit.id) == unit


def test_update_rejects_new_text_even_with_matching_hash(store, document_ids):
    unit = store.create(make_unit(document_ids[0]))
    text = "Replacement text"
    replacement = unit.model_copy(
        update={"text": text, "content_hash": sha256(text.encode("utf-8")).hexdigest()}
    )
    with pytest.raises(ImmutableTextUnitError, match="text, content_hash"):
        store.update(replacement)


def test_update_missing_unit_raises(store, document_ids):
    with pytest.raises(TextUnitNotFoundError):
        store.update(make_unit(document_ids[0]))


def test_delete_uncited_unit(store, document_ids):
    unit = store.create(make_unit(document_ids[0]))
    assert store.delete(unit.id) is True
    assert store.get(unit.id) is None
    assert store.delete(unit.id) is False
