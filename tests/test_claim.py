from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import Claim, ContributorRef, EvidenceRef


def make_evidence(supports):
    return EvidenceRef(
        text_unit_id=uuid4(), supports=supports, added_by="manual-review"
    )


def test_claim_starts_as_unverified_proposal():
    evidence = make_evidence(True)
    contributor = ContributorRef(contributor_id=uuid4())
    claim = Claim(
        statement="Nix provides development shells.",
        confidence=0.9,
        evidence=[evidence],
        contributors=[contributor],
    )

    assert claim.status == "proposed"
    assert claim.polarity == "uncertain"
    assert claim.verified_at is None
    assert claim.verified_by is None
    assert claim.created_at.utcoffset() == timedelta(0)
    assert claim.evidence == [evidence]
    assert claim.contributors == [contributor]
    assert Claim.model_validate_json(claim.model_dump_json()) == claim


@pytest.mark.parametrize("explicit_empty", [False, True])
def test_claim_rejects_missing_evidence(explicit_empty):
    kwargs = {"evidence": [], "counter_evidence": []} if explicit_empty else {}
    with pytest.raises(ValidationError, match="claim must include"):
        Claim(statement="An unsupported assertion.", confidence=0.5, **kwargs)


@pytest.mark.parametrize("with_support", [False, True])
def test_claim_preserves_counter_evidence(with_support):
    opposing = make_evidence(False)
    supporting = [make_evidence(True)] if with_support else []
    claim = Claim(
        statement="An assertion under dispute.",
        confidence=0.5,
        polarity="mixed" if with_support else "disputed",
        evidence=supporting,
        counter_evidence=[opposing],
    )
    assert claim.counter_evidence == [opposing]
    assert claim.evidence == supporting


@pytest.mark.parametrize(
    "field,supports,message",
    [
        ("evidence", False, "evidence must contain supporting references"),
        ("counter_evidence", True, "counter_evidence must contain opposing references"),
    ],
)
def test_claim_rejects_evidence_in_wrong_list(field, supports, message):
    with pytest.raises(ValidationError, match=message):
        Claim(
            statement="An assertion.",
            confidence=0.5,
            **{field: [make_evidence(supports)]},
        )


@pytest.mark.parametrize(
    "field,value",
    [("confidence", -0.1), ("confidence", 1.1), ("status", "active"), ("polarity", "verified")],
)
def test_claim_rejects_invalid_scores_and_states(field, value):
    data = {
        "statement": "An assertion.",
        "confidence": 0.5,
        "evidence": [make_evidence(True)],
        field: value,
    }
    with pytest.raises(ValidationError) as error:
        Claim(**data)
    assert error.value.errors()[0]["loc"] == (field,)

