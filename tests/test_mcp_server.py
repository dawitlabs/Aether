import json
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from agent_client import AetherClient  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_server import build  # noqa: E402


def server_with(responses):
    """responses: (status, body) per request; returns the server and sent requests."""
    sent = []

    def send(method, url, headers, data):
        sent.append((method, url, json.loads(data) if data else None))
        status, body = responses.pop(0)
        return status, {}, json.dumps(body).encode()

    return build(AetherClient("http://aether", api_key="ae_test", send=send)), sent


def run(server, name, arguments):
    async def go():
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            return tools, await client.call_tool(name, arguments)
    return anyio.run(go)


def test_tools_map_to_the_api():
    answer = {"answer": "Chadwick.", "citations": []}
    server, sent = server_with([(200, answer)])
    tools, result = run(server, "aether_query", {"question": "Who found the neutron?"})
    assert tools == {"aether_query", "aether_search_entities",
                     "aether_neighborhood", "aether_propose_claim"}
    assert not result.is_error
    assert sent == [("POST", "http://aether/api/v0/query",
                     {"question": "Who found the neutron?", "mode": "hybrid"})]
    assert "Chadwick." in result.content[0].text


def test_claim_evidence_is_sent_as_excerpts_and_api_errors_become_tool_errors():
    server, sent = server_with([(403, {"error": "forbidden", "detail": "needs propose"})])
    _, result = run(server, "aether_propose_claim", {
        "statement": "Radium glows.",
        "evidence": [{"text_unit_id": "u1", "excerpt": "radium glows"}],
    })
    assert sent[0][2]["evidence"] == [{"text_unit_id": "u1", "excerpt": "radium glows"}]
    assert result.is_error and "403 forbidden: needs propose" in result.content[0].text
