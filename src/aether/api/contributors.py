"""Contributor administration and self-lookup."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from aether.api.auth import current_contributor, require, writer
from aether.core.models import Contributor, Permission
from aether.storage.claims import Neo4jClaimStore
from aether.storage.contributors import Neo4jContributorStore

router = APIRouter()


class NewContributor(BaseModel):
    type: Literal["human", "agent"]
    display_name: str = Field(min_length=1, max_length=100)
    permissions: list[Permission] = Field(default_factory=lambda: ["propose"])


class ContributorProfile(BaseModel):
    contributor: Contributor
    accepted_claims: int
    rejected_claims: int


class NewKey(BaseModel):
    api_key: str = Field(description="Shown only once; the previous key stops working.")


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


def contributors(request: Request) -> Neo4jContributorStore:
    return Neo4jContributorStore(request.app.state.driver, request.app.state.config.neo4j_database)


@router.post("/contributors/me/key", responses={401: {}, 429: {}})
def rotate_key(
    request: Request, contributor: Annotated[Contributor, Depends(writer)]
) -> NewKey:
    """Replace your own API key, e.g. after a leak."""
    key = contributors(request).rotate_key(contributor.id)
    if key is None:
        raise HTTPException(404, "Contributor not found")
    return NewKey(api_key=key)


@router.post("/contributors/{contributor_id}/revoke", status_code=204,
             responses={401: {}, 403: {}, 404: {}})
def revoke_key(
    request: Request, contributor_id: UUID,
    _: Annotated[Contributor, Depends(require("admin"))],
) -> None:
    """Disable a contributor's key; their past work stays attributed."""
    if not contributors(request).revoke_key(contributor_id):
        raise HTTPException(404, "Contributor not found")


@router.get("/contributors/{contributor_id}", responses={404: {}})
def profile(request: Request, contributor_id: UUID) -> ContributorProfile:
    """Identity plus reputation: counts of this contributor's accepted and rejected claims."""
    state = request.app.state
    db = state.config.neo4j_database
    contributor = Neo4jContributorStore(state.driver, db).get(contributor_id)
    if contributor is None:
        raise HTTPException(404, "Contributor not found")
    accepted, rejected = Neo4jClaimStore(state.driver, db).reputation(contributor_id)
    return ContributorProfile(
        contributor=contributor, accepted_claims=accepted, rejected_claims=rejected
    )
