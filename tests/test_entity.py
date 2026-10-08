from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import Entity, ProvenanceRef


@pytest.fixture
def entity_data():
    return {
        "name": "Nix",
        "type": "Software",
        "description": "A package management tool.",
        "confidence": 0.9,
        "provenance": [
            ProvenanceRef(text_unit_id=uuid4(), extracted_by="manual-review")
        ],
    }


def test_entity_preserves_provenance_and_has_independent_defaults(entity_data):
    first = Entity(**entity_data)
    second = Entity(**entity_data)

    assert first.provenance == entity_data["provenance"]
    assert first.id != second.id
    assert first.status == "active"
    assert first.embedding is None
    assert first.merged_into_id is None
    assert first.first_seen.utcoffset() == timedelta(0)
    assert first.last_updated.utcoffset() == timedelta(0)

    first.aliases.append("Nix package manager")
    first.properties["category"] = "development"
    assert second.aliases == []
    assert second.properties == {}


@pytest.mark.parametrize("missing", [False, True])
def test_entity_requires_provenance(entity_data, missing):
    if missing:
        entity_data.pop("provenance")
    else:
        entity_data["provenance"] = []

    with pytest.raises(ValidationError) as error:
        Entity(**entity_data)
    assert error.value.errors()[0]["loc"] == ("provenance",)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_entity_rejects_out_of_range_confidence(entity_data, confidence):
    entity_data["confidence"] = confidence
    with pytest.raises(ValidationError) as error:
        Entity(**entity_data)
    assert error.value.errors()[0]["loc"] == ("confidence",)


@pytest.mark.parametrize("confidence", [0, 1])
def test_entity_accepts_confidence_boundaries(entity_data, confidence):
    entity_data["confidence"] = confidence
    assert Entity(**entity_data).confidence == confidence


def test_entity_rejects_undocumented_status(entity_data):
    entity_data["status"] = "verified"
    with pytest.raises(ValidationError) as error:
        Entity(**entity_data)
    assert error.value.errors()[0]["loc"] == ("status",)
