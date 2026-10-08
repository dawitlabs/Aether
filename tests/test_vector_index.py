import math
from uuid import uuid4

import pytest

from aether.storage.vectors import LanceVectorIndex


@pytest.fixture
def index(tmp_path):
    return LanceVectorIndex(tmp_path, model="test-model", dimensions=3)


def test_upsert_and_get_round_trip(index):
    source_id = uuid4()
    index.upsert(source_id, "text_unit", [0.5, -0.25, 1.0])
    assert index.get(source_id) == [0.5, -0.25, 1.0]
    assert index.get(uuid4()) is None


def test_repeated_upsert_replaces_instead_of_duplicating(index):
    source_id = uuid4()
    index.upsert(source_id, "entity", [1.0, 0.0, 0.0])
    index.upsert(source_id, "entity", [0.0, 1.0, 0.0])
    assert index.get(source_id) == [0.0, 1.0, 0.0]
    matches = index.search([0.0, 1.0, 0.0], limit=10)
    assert [match.source_id for match in matches] == [source_id]


def test_search_orders_by_distance(index):
    near, far = uuid4(), uuid4()
    index.upsert(far, "entity", [-1.0, 0.0, 0.0])
    index.upsert(near, "text_unit", [1.0, 0.1, 0.0])
    matches = index.search([1.0, 0.0, 0.0], limit=2)
    assert [(m.source_id, m.source_kind) for m in matches] == [(near, "text_unit"), (far, "entity")]
    assert matches[0].distance < matches[1].distance


def test_delete_removes_vector(index):
    source_id = uuid4()
    index.upsert(source_id, "entity", [1.0, 2.0, 3.0])
    index.delete(source_id)
    assert index.get(source_id) is None


def test_data_persists_across_reopen(tmp_path):
    source_id = uuid4()
    LanceVectorIndex(tmp_path, model="m", dimensions=2).upsert(source_id, "entity", [1.0, 2.0])
    assert LanceVectorIndex(tmp_path, model="m", dimensions=2).get(source_id) == [1.0, 2.0]


def test_models_and_dimensions_never_share_a_table(tmp_path):
    source_id = uuid4()
    LanceVectorIndex(tmp_path, model="a", dimensions=2).upsert(source_id, "entity", [1.0, 2.0])
    assert LanceVectorIndex(tmp_path, model="b", dimensions=2).get(source_id) is None
    assert LanceVectorIndex(tmp_path, model="a", dimensions=3).get(source_id) is None


@pytest.mark.parametrize(
    "vector,message",
    [([1.0, 2.0], "3 dimensions"), ([1.0, math.nan, 0.0], "finite"), ([math.inf, 0.0, 0.0], "finite")],
)
def test_invalid_vectors_are_rejected(index, vector, message):
    with pytest.raises(ValueError, match=message):
        index.upsert(uuid4(), "entity", vector)


@pytest.mark.parametrize(
    "kwargs",
    [{"model": "Bad Name", "dimensions": 3}, {"model": "x'; drop", "dimensions": 3},
     {"model": "ok", "dimensions": 0}, {"model": "ok", "dimensions": True}],
)
def test_invalid_index_configuration_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        LanceVectorIndex(tmp_path, **kwargs)


def test_unknown_source_kind_is_rejected(index):
    with pytest.raises(ValueError, match="source kind"):
        index.upsert(uuid4(), "claim", [1.0, 2.0, 3.0])
