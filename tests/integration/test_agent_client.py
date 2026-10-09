"""Phase 4 exit: an admin registers an agent, the agent proposes a claim, a
human accepts it, and the agent's query uses it, all via examples/agent_client.py."""

import importlib.util
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.extraction.pipeline import Extractor

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("agent_client", ROOT / "examples/agent_client.py")
agent_client = importlib.util.module_from_spec(spec)
sys.modules["agent_client"] = agent_client
spec.loader.exec_module(agent_client)


class Chat:
    """Extracts one person; answers citing every passage it was given."""

    model = "fake-chat"

    def __init__(self, name):
        self.name = name

    def chat_json(self, system, user):
        if system.startswith("Answer"):
            ids = [part.split('"')[0] for part in user.split('<passage id="')[1:]]
            return {"answer": f"{self.name} directed the lab.",
                    "citations": [{"text_unit_id": i, "quote": "directed the lab"} for i in ids]}
        return {"entities": [{"name": self.name, "type": "person", "description": "",
                              "excerpt": self.name, "confidence": 1}]}


class Embedder:
    model = "fake-embed"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def api(database, auth, make_contributor, tmp_path):
    driver, name = database
    settings = Settings.from_env().model_copy(
        update={"documents_dir": tmp_path / "docs", "index_dir": tmp_path / "index"})
    person = f"Ada{uuid4().hex[:6]}"
    with TestClient(create_app(settings)) as test_client:
        test_client.app.state.extractor = Extractor(
            Chat(person), Embedder(), test_client.app.state.extractor.store, tmp_path / "index")

        def send(method, url, headers, body):
            response = test_client.request(method, url, headers=headers, content=body)
            return response.status_code, dict(response.headers), response.content

        def client(key=None):
            return agent_client.AetherClient("http://testserver", key, send=send)

        admin = client(auth["Authorization"].removeprefix("Bearer "))
        _, reviewer_key = make_contributor(permissions=["review"])
        created = {"documents": [], "contributors": []}
        yield client, admin, client(reviewer_key), person, created
    driver.execute_query(
        "MATCH (t:TextUnit) WHERE t.source_document_id IN $docs "
        "OPTIONAL MATCH (t)<-[:CITES|OF]-(x) OPTIONAL MATCH (r:Review)-[:REVIEWS]->(x) "
        "DETACH DELETE r, x, t",
        parameters_={"docs": created["documents"]}, database_=name)
    driver.execute_query(
        "MATCH (n) WHERE (n:Document AND n.id IN $docs) OR (n:Contributor AND n.id IN $people) "
        "DETACH DELETE n",
        parameters_={"docs": created["documents"], "people": created["contributors"]},
        database_=name)


def test_registered_agent_proposes_human_accepts_agent_queries(api):
    client, admin, reviewer, person, created = api
    registered = admin.request("POST", "/contributors",
                               {"type": "agent", "display_name": "research-bot"})
    created["contributors"].append(registered["contributor"]["id"])
    agent = client(registered["api_key"])
    assert agent.me()["type"] == "agent"

    document = admin.upload(f"{person} directed the lab in 1909. {uuid4()}", "lab.txt")
    created["documents"].append(document["id"])
    admin.request("POST", f"/documents/{document['id']}/extraction")
    entity = None
    for _ in range(100):
        found = agent.search_entities(person)
        if found:
            entity = found[0]
            break
        time.sleep(0.05)
    unit = agent.neighborhood(entity["id"])["text_units"][0]["id"]

    proposed = agent.propose_claim(f"{person} directed the lab.", [(unit, "directed the lab")],
                                   subject_id=entity["id"], confidence=0.9)
    claim_id = proposed["claim"]["id"]
    with pytest.raises(agent_client.AetherError) as denied:
        agent.request("POST", f"/claims/{claim_id}/review", {"decision": "accept"})
    assert (denied.value.status, denied.value.error) == (403, "forbidden")

    reviewer.request("POST", f"/claims/{claim_id}/review", {"decision": "accept"})
    answer = agent.query(f"What did {person} direct?")

    assert claim_id in answer["claim_ids"]
    assert answer["citations"][0]["text_unit_id"] == unit
    assert agent.claim(claim_id)["claim"]["status"] == "verified"
