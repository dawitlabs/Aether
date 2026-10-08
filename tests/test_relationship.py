from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import ProvenanceRef, Relationship


@pytest.fixture
def relationship_data():
    return {
        "source_id": uuid4(),
        "target_id": uuid4(),
        "type": "DEPENDS_ON",
        "description": "The source component depends on the target component.",
        "weight": 0.8,
        "provenance": [
            ProvenanceRef(text_unit_id=uuid4(), extracted_by="manual-review")
        ],
    }


def test_relationship_preserves_direction_and_provenance(relationship_data):
    relationship = Relationship(**relationship_data)

    assert relationship.source_id == relationship_data["source_id"]
    assert relationship.target_id == relationship_data["target_id"]
    assert relationship.provenance == relationship_data["provenance"]
    assert relationship.status == "active"
    assert relationship.valid_from is None
    assert relationship.valid_to is None
    assert relationship.created_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("missing", [False, True])
def test_relationship_requires_provenance(relationship_data, missing):
    if missing:
        relationship_data.pop("provenance")
    else:
        relationship_data["provenance"] = []

    with pytest.raises(ValidationError) as error:
        Relationship(**relationship_data)
    assert error.value.errors()[0]["loc"] == ("provenance",)


@pytest.mark.parametrize("status", ["active", "disputed", "deprecated"])
def test_relationship_accepts_documented_statuses(relationship_data, status):
    relationship_data["status"] = status
    assert Relationship(**relationship_data).status == status


def test_relationship_rejects_undocumented_status(relationship_data):
    relationship_data["status"] = "verified"
    with pytest.raises(ValidationError) as error:
        Relationship(**relationship_data)
    assert error.value.errors()[0]["loc"] == ("status",)
