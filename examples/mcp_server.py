"""Aether as an MCP server, so agent tools (Claude Code, Cursor, OpenClaw, ...)
can query the graph and propose claims. Talks to a running Aether API over
HTTP via agent_client.py; stdio transport.

    pip install -e ".[mcp]"
    AETHER_URL=http://127.0.0.1:8000 AETHER_API_KEY=ae_... python examples/mcp_server.py

AETHER_API_KEY is optional for querying; proposing claims needs a key with
the `propose` permission.
"""

import os
from typing import Any, Literal

from agent_client import AetherClient, AetherError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    text_unit_id: str = Field(description="ID of a cited passage, from a query or neighborhood")
    excerpt: str = Field(description="Verbatim text from that passage supporting the claim")


def build(aether: AetherClient) -> MCPServer:
    server = MCPServer(
        "aether",
        instructions=(
            "Aether is a knowledge graph where every fact cites source text. Prefer "
            "aether_query for questions; an answer with no citations means the graph "
            "has no support for it. Propose claims only with verbatim evidence."
        ),
    )

    def call(method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(aether, method)(*args, **kwargs)
        except AetherError as error:
            raise ToolError(f"Aether {error.status} {error.error}: {error.detail}") from None
        except OSError:
            raise ToolError(f"Aether API unreachable at {aether.base}") from None

    @server.tool()
    def aether_query(question: str,
                     mode: Literal["local", "global", "hybrid"] = "hybrid") -> dict[str, Any]:
        """Answer a question from the graph with verified citations. local: about
        specific entities; global: themes across the corpus; hybrid: both."""
        return call("query", question, mode)

    @server.tool()
    def aether_search_entities(name: str) -> list[dict[str, Any]]:
        """Find entities whose name contains the given text."""
        return call("search_entities", name)

    @server.tool()
    def aether_neighborhood(entity_id: str) -> dict[str, Any]:
        """An entity's relationships, neighbours, and the passages that cite them."""
        return call("neighborhood", entity_id)

    @server.tool()
    def aether_propose_claim(statement: str, evidence: list[Evidence],
                             confidence: float = 0.8) -> dict[str, Any]:
        """Propose a claim for human review, backed by verbatim passage excerpts.
        It affects answers only after a reviewer accepts it."""
        return call("propose_claim", statement,
                    [(e.text_unit_id, e.excerpt) for e in evidence], confidence=confidence)

    return server


if __name__ == "__main__":
    build(AetherClient(os.environ.get("AETHER_URL", "http://127.0.0.1:8000"),
                       api_key=os.environ.get("AETHER_API_KEY"))).run()
