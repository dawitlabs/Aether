"""Operational stats for admins: graph counts and in-process counters."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from aether.api.auth import require
from aether.core.models import Contributor

router = APIRouter()
STARTED = time.monotonic()


@router.get("/admin/stats", responses={401: {}, 403: {}})
def stats(
    request: Request, _: Annotated[Contributor, Depends(require("admin"))]
) -> dict[str, object]:
    state = request.app.state
    db = state.config.neo4j_database
    graph, _, _ = state.driver.execute_query(
        "RETURN COUNT { (:Document) } AS documents, COUNT { (:TextUnit) } AS text_units, "
        "COUNT { (e:Entity WHERE e.status = 'active') } AS active_entities, "
        "COUNT { (:Relationship) } AS relationships, COUNT { (:Community) } AS communities, "
        "COUNT { (:Contributor) } AS contributors, "
        "COUNT { (m:MergeCandidate WHERE m.status = 'open') } AS open_merge_candidates",
        database_=db, routing_="r",
    )
    claims, _, _ = state.driver.execute_query(
        "MATCH (c:Claim) RETURN c.status AS status, count(*) AS n",
        database_=db, routing_="r",
    )
    chat = state.extractor.chat
    return {
        "graph": graph[0].data(),
        "claims": {r["status"]: r["n"] for r in claims},
        "requests": dict(state.requests),
        "llm": {"calls": chat.calls, "cache_hits": chat.cache_hits},
        "uptime_seconds": round(time.monotonic() - STARTED),
    }
