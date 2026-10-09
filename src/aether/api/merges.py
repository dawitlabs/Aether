"""Entity merge review: humans decide uncertain look-alike pairs."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from aether.api.auth import require
from aether.core.models import Contributor, MergeCandidate
from aether.storage.merges import CandidateClosedError, CandidateNotFoundError, Neo4jMergeStore

router = APIRouter()


class MergeDecision(BaseModel):
    decision: Literal["merge", "keep_separate"]


def store(request: Request) -> Neo4jMergeStore:
    return Neo4jMergeStore(request.app.state.driver, request.app.state.config.neo4j_database)


@router.get("/merge-candidates")
def list_candidates(
    request: Request,
    status: Literal["open", "merged", "kept_separate"] | None = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MergeCandidate]:
    return store(request).find(status=status, limit=limit)


@router.post("/merge-candidates/{candidate_id}/review",
             responses={401: {}, 403: {}, 404: {}, 409: {}})
def review_candidate(
    request: Request, candidate_id: UUID, body: MergeDecision,
    reviewer: Annotated[Contributor, Depends(require("review"))],
) -> MergeCandidate:
    """Merge folds the newer entity into the older one, keeping history."""
    if reviewer.type != "human":
        raise HTTPException(403, "Only humans may review merges")
    try:
        return store(request).review(candidate_id, reviewer.id, body.decision)
    except CandidateNotFoundError:
        raise HTTPException(404, "Merge candidate not found") from None
    except CandidateClosedError as error:
        raise HTTPException(409, str(error)) from None
