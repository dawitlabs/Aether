"""Contributor administration and self-lookup."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from aether.api.auth import current_contributor, require
from aether.core.models import Contributor, Permission
from aether.storage.contributors import Neo4jContributorStore

router = APIRouter()


class NewContributor(BaseModel):
    type: Literal["human", "agent"]
    display_name: str = Field(min_length=1, max_length=100)
    permissions: list[Permission] = Field(default_factory=lambda: ["propose"])


class CreatedContributor(BaseModel):
    contributor: Contributor
    api_key: str = Field(description="Shown only once; store it securely.")


@router.post("/contributors", status_code=201, responses={401: {}, 403: {}})
def create_contributor(
    request: Request,
    body: NewContributor,
    _: Annotated[Contributor, Depends(require("admin"))],
) -> CreatedContributor:
    try:
        contributor = Contributor(**body.model_dump())
    except ValidationError as error:
        raise HTTPException(422, error.errors()[0]["msg"]) from None
    state = request.app.state
    key = Neo4jContributorStore(state.driver, state.config.neo4j_database).create(contributor)
    return CreatedContributor(contributor=contributor, api_key=key)


@router.get("/contributors/me", responses={401: {}})
def me(contributor: Annotated[Contributor, Depends(current_contributor)]) -> Contributor:
    return contributor
