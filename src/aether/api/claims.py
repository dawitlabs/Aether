"""Claim proposal, review, and dispute routes (ADR-0002)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from aether.api.auth import require
from aether.core.models import Claim, ClaimStatus, Contributor, EvidenceRef, Review
from aether.storage.claims import (
    ClaimNotFoundError,
    InvalidEvidenceError,
    InvalidTransitionError,
    Neo4jClaimStore,
    SelfReviewError,
)
from aether.storage.knowledge import MissingReferenceError

router = APIRouter()
ERRORS = {401: {}, 403: {}, 404: {}, 409: {}, 422: {}}


class EvidenceIn(BaseModel):
    text_unit_id: UUID
    excerpt: str = Field(min_length=1, max_length=2000)


class ClaimProposal(BaseModel):
    statement: str = Field(min_length=1, max_length=1000)
    subject_id: UUID | None = None
    predicate: str | None = Field(default=None, min_length=1, max_length=100)
    object_id: UUID | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceIn] = Field(min_length=1, max_length=20)


class ReviewIn(BaseModel):
    decision: Literal["accept", "reject"]
    notes: str = Field(default="", max_length=2000)


class DisputeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    counter_evidence: list[EvidenceIn] = Field(min_length=1, max_length=20)


class ClaimOut(BaseModel):
    claim: Claim
    author_id: UUID | None
    reviews: list[Review]


def store(request: Request) -> Neo4jClaimStore:
    return Neo4jClaimStore(request.app.state.driver, request.app.state.config.neo4j_database)


def refs(items: list[EvidenceIn], *, supports: bool, by: Contributor) -> list[EvidenceRef]:
    return [EvidenceRef(text_unit_id=i.text_unit_id, excerpt=i.excerpt, supports=supports,
                        added_by=str(by.id)) for i in items]


@contextmanager
def claim_errors() -> Iterator[None]:
    try:
        yield
    except ClaimNotFoundError:
        raise HTTPException(404, "Claim not found") from None
    except InvalidTransitionError as error:
        raise HTTPException(409, str(error)) from None
    except SelfReviewError as error:
        raise HTTPException(403, str(error)) from None
    except (InvalidEvidenceError, MissingReferenceError) as error:
        raise HTTPException(422, str(error)) from None


def load(request: Request, claim_id: UUID) -> ClaimOut:
    record = store(request).get(claim_id)
    if record is None:
        raise HTTPException(404, "Claim not found")
    return ClaimOut(claim=record.claim, author_id=record.author_id, reviews=record.reviews)


@router.post("/claims", status_code=201, responses=ERRORS)
def propose(
    request: Request, body: ClaimProposal,
    author: Annotated[Contributor, Depends(require("propose"))],
) -> ClaimOut:
    """Propose a claim. Every excerpt must appear verbatim in its text unit."""
    claim = Claim(
        statement=body.statement, subject_id=body.subject_id, predicate=body.predicate,
        object_id=body.object_id, confidence=body.confidence,
        evidence=refs(body.evidence, supports=True, by=author),
    )
    with claim_errors():
        store(request).propose(claim, author.id)
    return load(request, claim.id)


@router.get("/claims")
def list_claims(
    request: Request,
    status: ClaimStatus | None = None,
    entity_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Claim]:
    return store(request).find(status=status, entity_id=entity_id, limit=limit)


@router.get("/claims/{claim_id}", responses={404: {}})
def get_claim(request: Request, claim_id: UUID) -> ClaimOut:
    return load(request, claim_id)


@router.post("/claims/{claim_id}/review", responses=ERRORS)
def review(
    request: Request, claim_id: UUID, body: ReviewIn,
    reviewer: Annotated[Contributor, Depends(require("review"))],
) -> ClaimOut:
    """Accept or reject. Humans with the review permission only, never the author."""
    if reviewer.type != "human":
        raise HTTPException(403, "Only humans may review claims")
    with claim_errors():
        store(request).review(claim_id, reviewer.id, body.decision, body.notes)
    return load(request, claim_id)


@router.post("/claims/{claim_id}/dispute", responses=ERRORS)
def dispute(
    request: Request, claim_id: UUID, body: DisputeIn,
    contributor: Annotated[Contributor, Depends(require("propose"))],
) -> ClaimOut:
    """Attach counter-evidence; the claim returns to review."""
    with claim_errors():
        store(request).dispute(claim_id, contributor.id, body.reason,
                               refs(body.counter_evidence, supports=False, by=contributor))
    return load(request, claim_id)
