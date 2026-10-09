"""End-to-end demo of the agent workflow, in-process (no port), real models.

    python scripts/demo.py [--domain radioactivity]

An admin uploads a domain pack and builds the graph; a registered agent asks
questions, proposes a claim from a passage, and sees it used once a human
reviewer accepts it. The demo's contributors and claim are deleted at the end;
the pack's documents and graph stay (uploads are idempotent).
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from neo4j import GraphDatabase

from aether.api.app import Settings, create_app
from aether.core.models import Contributor
from aether.domains import available, load_pack
from aether.storage.contributors import Neo4jContributorStore

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_client", ROOT / "examples/agent_client.py")
agent_client = importlib.util.module_from_spec(spec)
sys.modules["agent_client"] = agent_client
spec.loader.exec_module(agent_client)


def wait(check, seconds: float = 600) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError("background job did not finish")
        time.sleep(1)


def run(client, admin, reviewer, pack, created: list[str]) -> None:
    """Appends the IDs of contributors and claims it creates to `created`."""
    print(f"## {pack.name}: {len(pack.sources)} documents")
    documents = {}
    for path in pack.corpus():
        doc = documents[path.name] = admin.upload(path.read_text(), path.name)
        admin.request("POST", f"/documents/{doc['id']}/extraction")
        wait(lambda: admin.request("GET", f"/documents/{doc['id']}/extraction")["status"]
             in ("complete", "failed"))
    admin.request("POST", "/communities/rebuild")
    wait(lambda: admin.request("GET", "/communities/rebuild")["status"] != "running")
    print(f"graph: {admin.request('GET', '/admin/stats')['graph']}")

    registered = admin.request("POST", "/contributors",
                               {"type": "agent", "display_name": "demo-agent"})
    created.append(registered["contributor"]["id"])
    agent = client(registered["api_key"])

    for question, mode in [("Who discovered the neutron?", "local"),
                           ("What themes connect these documents?", "global")]:
        answer = agent.query(question, mode)
        print(f"\n[{mode}] {question}\n  {answer['answer']}")
        for citation in answer["citations"]:
            print(f"  cites: \"{citation['quote']}\"")

    rutherford = agent.search_entities("Ernest Rutherford")[0]
    document = documents["ernest-rutherford.txt"]["id"]
    units = agent.request("GET", f"/documents/{document}/text-units")
    unit = next(u for u in units if "Cavendish Laboratory" in u["text"])
    claim = agent.propose_claim(
        "Ernest Rutherford became Director of the Cavendish Laboratory in 1919.",
        [(unit["id"], "Rutherford became Director of the Cavendish Laboratory")],
        subject_id=rutherford["id"], confidence=0.95,
    )["claim"]
    created.append(claim["id"])
    print(f"\nagent proposed claim: {claim['status']}")
    reviewed = reviewer.request("POST", f"/claims/{claim['id']}/review",
                                {"decision": "accept", "notes": "Matches the source."})
    print(f"human review: {reviewed['claim']['status']}")
    answer = agent.query("When did Ernest Rutherford lead the Cavendish Laboratory?", "local")
    print(f"[local] {answer['answer']}\n  claim used: {claim['id'] in answer['claim_ids']}")


def main(domain: str) -> None:
    pack = load_pack(domain)
    config = Settings.from_env()
    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_username, config.neo4j_password)
    ) as driver, TestClient(create_app(config)) as test_client:
        db = config.neo4j_database
        people = Neo4jContributorStore(driver, db)
        admin = Contributor(type="human", display_name="demo-admin",
                            permissions=["propose", "review", "admin"])
        reviewer = Contributor(type="human", display_name="demo-reviewer",
                               permissions=["review"])
        keys = {"admin": people.create(admin), "reviewer": people.create(reviewer)}

        def send(method, url, headers, body):
            response = test_client.request(method, url, headers=headers, content=body)
            return response.status_code, dict(response.headers), response.content

        def client(key):
            return agent_client.AetherClient("http://testserver", key, send=send)

        created = [str(admin.id), str(reviewer.id)]
        try:
            run(client, client(keys["admin"]), client(keys["reviewer"]), pack, created)
        finally:
            driver.execute_query(
                "MATCH (n) WHERE (n:Contributor OR n:Claim) AND n.id IN $ids "
                "OPTIONAL MATCH (r:Review)-[:REVIEWS]->(n) DETACH DELETE r, n",
                ids=created, database_=db,
            )
            print("\ndemo contributors and claim removed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="radioactivity", choices=available())
    main(parser.parse_args().domain)
