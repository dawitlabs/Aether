"""Graph read and question-answering routes."""

import logging
from concurrent.futures import Future
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from aether.api.auth import require
from aether.communities.reports import rebuild_with_reports
from aether.core.models import Community, Entity, Relationship, TextUnit
from aether.query import Answer, Mode, answer_question
from aether.storage.claims import Neo4jClaimStore
from aether.storage.communities import Neo4jCommunityStore
from aether.storage.graph import Neo4jGraphReader

log = logging.getLogger("aether.api")
router = APIRouter()
UNAVAILABLE = {"description": "Database unavailable"}
REBUILD = "communities"


class NeighborhoodOut(BaseModel):
    entity: Entity
    neighbors: list[Entity]
    relationships: list[Relationship]
    text_units: list[TextUnit]


class RebuildStatus(BaseModel):
    status: Literal["not_started", "running", "failed", "complete"]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    mode: Mode = "local"


def public(entity: Entity) -> Entity:
    """Embeddings are internal and large; never return them."""
    return entity.model_copy(update={"embedding": None})


def reader(request: Request) -> Neo4jGraphReader:
    return Neo4jGraphReader(request.app.state.driver, request.app.state.config.neo4j_database)


@router.get("/entities", responses={503: UNAVAILABLE})
def search_entities(
    request: Request,
    name: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Entity]:
    graph = reader(request)
    return [public(e) for e in graph.search_entities(name, limit=limit)]


@router.get("/entities/{entity_id}/neighborhood", responses={404: {}, 503: UNAVAILABLE})
def get_neighborhood(request: Request, entity_id: UUID) -> NeighborhoodOut:
    """The entity, its relationships and their other ends, and every cited text unit."""
    graph = reader(request)
    hood = graph.neighborhood(entity_id)
    if hood is None:
        raise HTTPException(404, "Entity not found")
    return NeighborhoodOut(
        entity=public(hood.entity),
        neighbors=[public(e) for e in hood.neighbors],
        relationships=hood.relationships,
        text_units=hood.text_units,
    )


@router.post("/query", responses={502: {"description": "Model provider unavailable"}})
def query(request: Request, body: Question) -> Answer:
    """Answer with verified quotes. Modes: local (entities and their verified
    claims), global (community reports), or hybrid (both)."""
    extractor = request.app.state.extractor
    return answer_question(
        body.question,
        body.mode,
        chat=extractor.chat,
        embedder=extractor.embedder,
        graph=reader(request),
        communities=community_store(request),
        claims=Neo4jClaimStore(request.app.state.driver, request.app.state.config.neo4j_database),
        index_path=request.app.state.config.index_dir,
    )


def log_failure(job: Future[object]) -> None:
    error = None if job.cancelled() else job.exception()
    if error is not None:
        log.warning("job.failed error=%s", type(error).__name__)


def community_store(request: Request) -> Neo4jCommunityStore:
    return Neo4jCommunityStore(request.app.state.driver, request.app.state.config.neo4j_database)


def rebuild_status(request: Request) -> RebuildStatus:
    job: Future[object] | None = request.app.state.jobs.get(REBUILD)
    if job is None or job.cancelled():
        return RebuildStatus(status="not_started")
    if not job.done():
        return RebuildStatus(status="running")
    return RebuildStatus(status="failed" if job.exception() else "complete")


@router.post(
    "/communities/rebuild", status_code=202, dependencies=[Depends(require("propose"))]
)
def start_rebuild(request: Request) -> RebuildStatus:
    """Replace all communities and their reports. Queued behind any running extraction."""
    state = request.app.state
    job = state.jobs.get(REBUILD)
    if job is None or job.done():
        job = state.worker.submit(
            rebuild_with_reports, community_store(request),
            state.extractor.chat, state.extractor.embedder, state.config.index_dir,
        )
        job.add_done_callback(log_failure)
        state.jobs[REBUILD] = job
    return rebuild_status(request)


@router.get("/communities/rebuild")
def get_rebuild(request: Request) -> RebuildStatus:
    return rebuild_status(request)


@router.get("/communities", responses={503: UNAVAILABLE})
def list_communities(request: Request) -> list[Community]:
    return community_store(request).list_all()
