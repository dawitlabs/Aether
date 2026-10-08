"""Graph read and question-answering routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from aether.core.models import Entity, Relationship, TextUnit
from aether.query import Answer, answer_question
from aether.storage.graph import Neo4jGraphReader

router = APIRouter()
UNAVAILABLE = {"description": "Database unavailable"}


class NeighborhoodOut(BaseModel):
    entity: Entity
    neighbors: list[Entity]
    relationships: list[Relationship]
    text_units: list[TextUnit]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


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
    """Answer from the nearest entities' neighborhoods; citations are text-unit IDs."""
    extractor = request.app.state.extractor
    return answer_question(
        body.question,
        chat=extractor.chat,
        embedder=extractor.embedder,
        graph=reader(request),
        index_path=request.app.state.config.index_dir,
    )
