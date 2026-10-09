from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app

API = "http://testserver/api/v0"

PLAIN = {"content-type": "text/plain; charset=utf-8"}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def setup(database, make_contributor, tmp_path):
    """An uploaded passage plus an agent, two human reviewers, and a plain human."""
    driver, name = database
    people = {
        "agent": make_contributor(type="agent", display_name="bot"),
        "alice": make_contributor(permissions=["propose", "review"], display_name="alice"),
        "bob": make_contributor(permissions=["propose", "review"], display_name="bob"),
        "carol": make_contributor(permissions=["propose"], display_name="carol"),
    }
    text = f"Marie Curie was born in Warsaw. Tag {uuid4()}."
    settings = Settings.from_env().model_copy(update={"documents_dir": tmp_path})
    with TestClient(create_app(settings), base_url=API) as client:
        document = client.post("/documents", content=text, headers={
            **PLAIN, **bearer(people["alice"][1])}).json()
        unit = client.get(f"/documents/{document['id']}/text-units").json()[0]
        yield client, unit["id"], {k: bearer(v[1]) for k, v in people.items()}, \
            {k: str(v[0].id) for k, v in people.items()}
    driver.execute_query(
        "MATCH (t:TextUnit {source_document_id: $doc})<-[:CITES]-(c:Claim) "
        "OPTIONAL MATCH (r:Review)-[:REVIEWS]->(c) DETACH DELETE r, c",
        parameters_={"doc": document["id"]}, database_=name,
    )
    driver.execute_query(
        "MATCH (n) WHERE (n:Document AND n.id = $doc) "
        "OR (n:TextUnit AND n.source_document_id = $doc) DELETE n",
        parameters_={"doc": document["id"]}, database_=name,
    )


def propose(client, headers, unit, excerpt="born in Warsaw", **fields):
    return client.post("/claims", headers=headers, json={
        "statement": "Marie Curie was born in Warsaw.", "confidence": 0.9,
        "evidence": [{"text_unit_id": unit, "excerpt": excerpt}], **fields,
    })


def test_agent_proposal_records_author_and_starts_proposed(setup):
    client, unit, keys, ids = setup
    response = propose(client, keys["agent"], unit, excerpt="BORN  in warsaw")
    assert response.status_code == 201
    body = response.json()
    assert (body["claim"]["status"], body["author_id"]) == ("proposed", ids["agent"])
    assert body["claim"]["evidence"][0]["added_by"] == ids["agent"]


@pytest.mark.parametrize("change", [
    {"excerpt": "born in Paris"},
    {"excerpt": "   "},
    {"excerpt": "a"},
    {"unit": str(uuid4())},
    {"subject_id": str(uuid4())},
])
def test_proposal_rejects_unsupported_evidence_or_missing_entities(setup, change):
    client, unit, keys, _ = setup
    unit = change.pop("unit", unit)
    assert propose(client, keys["agent"], unit, **change).status_code == 422


def test_proposal_requires_evidence_and_a_key(setup):
    client, unit, keys, _ = setup
    no_evidence = client.post("/claims", headers=keys["agent"], json={
        "statement": "x", "confidence": 0.5, "evidence": []})
    assert no_evidence.status_code == 422
    assert propose(client, {}, unit).status_code == 401


def test_review_rules(setup):
    client, unit, keys, ids = setup
    claim = propose(client, keys["alice"], unit).json()["claim"]["id"]
    review = f"/claims/{claim}/review"

    assert client.post(review, headers=keys["agent"], json={"decision": "accept"}).status_code == 403
    assert client.post(review, headers=keys["carol"], json={"decision": "accept"}).status_code == 403
    assert client.post(review, headers=keys["alice"], json={"decision": "accept"}).status_code == 403

    accepted = client.post(review, headers=keys["bob"], json={"decision": "accept", "notes": "ok"})
    assert accepted.status_code == 200
    body = accepted.json()
    assert (body["claim"]["status"], body["claim"]["polarity"]) == ("verified", "supported")
    assert body["claim"]["verified_by"] == ids["bob"]
    assert [(r["kind"], r["contributor_id"]) for r in body["reviews"]] == [("accept", ids["bob"])]

    again = client.post(review, headers=keys["bob"], json={"decision": "reject"})
    assert again.status_code == 409
    assert client.post(f"/claims/{uuid4()}/review", headers=keys["bob"],
                       json={"decision": "accept"}).status_code == 404


def test_dispute_reopens_review_and_acceptance_becomes_mixed(setup):
    client, unit, keys, _ = setup
    claim = propose(client, keys["agent"], unit).json()["claim"]["id"]
    client.post(f"/claims/{claim}/review", headers=keys["alice"], json={"decision": "accept"})

    for excerpt in ("born in Paris", " "):
        bad = client.post(f"/claims/{claim}/dispute", headers=keys["agent"], json={
            "reason": "x", "counter_evidence": [{"text_unit_id": unit, "excerpt": excerpt}]})
        assert bad.status_code == 422

    disputed = client.post(f"/claims/{claim}/dispute", headers=keys["carol"], json={
        "reason": "Tag suggests a test fixture.",
        "counter_evidence": [{"text_unit_id": unit, "excerpt": "Tag"}],
    }).json()
    assert (disputed["claim"]["status"], disputed["claim"]["polarity"]) == (
        "under_review", "disputed")
    assert disputed["claim"]["verified_at"] is None
    assert [r["kind"] for r in disputed["reviews"]] == ["accept", "dispute"]
    assert disputed["claim"]["counter_evidence"][0]["excerpt"] == "Tag"

    final = client.post(f"/claims/{claim}/review", headers=keys["bob"],
                        json={"decision": "accept"}).json()
    assert (final["claim"]["status"], final["claim"]["polarity"]) == ("verified", "mixed")


def test_reputation_and_listing(setup):
    client, unit, keys, ids = setup
    kept = propose(client, keys["agent"], unit).json()["claim"]["id"]
    dropped = propose(client, keys["agent"], unit).json()["claim"]["id"]
    client.post(f"/claims/{kept}/review", headers=keys["alice"], json={"decision": "accept"})
    client.post(f"/claims/{dropped}/review", headers=keys["alice"], json={"decision": "reject"})

    profile = client.get(f"/contributors/{ids['agent']}").json()
    assert (profile["accepted_claims"], profile["rejected_claims"]) == (1, 1)
    verified = [c["id"] for c in client.get("/claims", params={"status": "verified",
                                                               "limit": 200}).json()]
    assert kept in verified and dropped not in verified
    assert client.get(f"/contributors/{uuid4()}").status_code == 404
