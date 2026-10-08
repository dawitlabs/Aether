from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, JsonValue, model_validator


class ProvenanceRef(BaseModel):
    text_unit_id: UUID
    excerpt: str | None = None
    relevance: float | None = Field(default=None, ge=0, le=1)
    extracted_by: str = Field(min_length=1)
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EvidenceRef(BaseModel):
    text_unit_id: UUID
    excerpt: str | None = None
    supports: bool
    relevance: float | None = Field(default=None, ge=0, le=1)
    added_by: str = Field(min_length=1)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContributorRef(BaseModel):
    """Minimal ID reference; the documentation does not define this shape."""

    contributor_id: UUID


class Claim(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1)
    subject_id: UUID | None = None
    predicate: str | None = None
    object_id: UUID | None = None
    confidence: float = Field(ge=0, le=1)
    polarity: Literal["supported", "disputed", "uncertain", "mixed"] = "uncertain"
    status: Literal[
        "proposed", "under_review", "verified", "rejected", "superseded"
    ] = "proposed"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    counter_evidence: list[EvidenceRef] = Field(default_factory=list)
    supersedes_id: UUID | None = None
    superseded_by_id: UUID | None = None
    contributors: list[ContributorRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime | None = None
    verified_by: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if not self.evidence and not self.counter_evidence:
            raise ValueError("claim must include supporting or counter-evidence")
        if any(not ref.supports for ref in self.evidence):
            raise ValueError("evidence must contain supporting references")
        if any(ref.supports for ref in self.counter_evidence):
            raise ValueError("counter_evidence must contain opposing references")
        return self


class Entity(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    embedding: list[float] | None = None
    confidence: float = Field(ge=0, le=1)
    status: Literal["active", "merged", "deprecated"] = "active"
    merged_into_id: UUID | None = None
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provenance: list[ProvenanceRef] = Field(min_length=1)


class Relationship(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    target_id: UUID
    type: str = Field(min_length=1)
    description: str
    weight: float
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: Literal["active", "disputed", "deprecated"] = "active"
    provenance: list[ProvenanceRef] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TextUnit(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    source_document_id: UUID
    token_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_uri: str | None = None
    media_type: str = Field(min_length=1)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    embedding: list[float] | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.start_offset is not None and self.end_offset is not None:
            if self.end_offset <= self.start_offset:
                raise ValueError("end_offset must be greater than start_offset")
        return self

    @model_validator(mode="after")
    def validate_content_hash(self) -> Self:
        expected_hash = sha256(self.text.encode("utf-8")).hexdigest()

        if self.content_hash != expected_hash:
            raise ValueError("content_hash must match text")

        return self


class Document(BaseModel):
    """An uploaded original; identical bytes always map to one document."""

    id: UUID = Field(default_factory=uuid4)
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    media_type: Literal["text/plain"] = "text/plain"
    size_bytes: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_unit_count: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Community(BaseModel):
    """A Leiden cluster of entities; derived data, replaced on every rebuild."""

    id: UUID = Field(default_factory=uuid4)
    entity_ids: list[UUID] = Field(min_length=2)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
