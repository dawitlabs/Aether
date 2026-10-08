"""Extract one text unit into the graph: extract, resolve entities, write.

Resolution reuses an existing active entity of the same type when its name key
matches exactly, or when its embedding is close enough. Otherwise a new entity
is created. Units are meant to be processed one at a time: concurrent runs can
create duplicate entities, but never a duplicate extraction of one unit.

Vectors are upserted after the graph commit. If that fails, entity embeddings
remain in Neo4j, so the index can be rebuilt from there.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from aether.core.models import Entity, ProvenanceRef, Relationship, TextUnit
from aether.extraction.extract import PROMPT_VERSION, EntityCandidate, extract
from aether.extraction.llm import LLMClient
from aether.storage.knowledge import DuplicateRecordError, Neo4jKnowledgeStore, name_key
from aether.storage.vectors import LanceVectorIndex

# ponytail: one global cosine threshold, checked on six all-minilm pairs only:
# "Acme Corp"/"Acme Corporation" 0.96, "University of Paris"/"Sorbonne" 0.93,
# "Marie"/"Pierre Curie" 0.84. A wrong merge is worse than a duplicate, so it
# errs high. Recalibrate on a labelled set when changing the embedding model.
MERGE_SIMILARITY = 0.95


def unit_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def open_index(path: Path, embed_model: str, dimensions: int) -> LanceVectorIndex:
    return LanceVectorIndex(path, model=embed_model.replace(":", "-").lower(), dimensions=dimensions)


def embedding_text(entity: EntityCandidate) -> str:
    return f"{entity.name} ({entity.type}): {entity.description}"


@dataclass
class Extractor:
    chat: LLMClient
    embedder: LLMClient
    store: Neo4jKnowledgeStore
    index_path: Path
    merge_similarity: float = MERGE_SIMILARITY
    _index: LanceVectorIndex | None = field(default=None, init=False)

    @property
    def marker(self) -> str:
        return f"{PROMPT_VERSION}/{self.chat.model}"

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        vectors = [unit_vector(v) for v in self.embedder.embed(texts)] if texts else []
        if vectors and self._index is None:
            self._index = open_index(self.index_path, self.embedder.model, len(vectors[0]))
        return vectors

    def _match(self, candidate: EntityCandidate, vector: list[float]) -> UUID | None:
        exact = self.store.find_entity(candidate.name, candidate.type)
        if exact or self._index is None:
            return exact
        # Unit vectors: LanceDB's squared L2 distance d gives cosine 1 - d/2.
        near = [
            m.source_id for m in self._index.search(vector, limit=5)
            if m.source_kind == "entity" and 1 - m.distance / 2 >= self.merge_similarity
        ]
        if not near:
            return None
        allowed = self.store.active_entity_ids(near, candidate.type)
        return next((i for i in near if i in allowed), None)

    def run(self, unit: TextUnit) -> bool:
        """Return False when this unit was already extracted with this marker."""
        if self.store.is_extracted(unit.id, self.marker):
            return False
        result = extract(self.chat, unit.text)
        vectors = self._vectors([embedding_text(c) for c in result.entities])

        def ref(excerpt: str, confidence: float) -> ProvenanceRef:
            return ProvenanceRef(
                text_unit_id=unit.id, excerpt=excerpt, relevance=confidence,
                extracted_by=self.marker,
            )

        ids: dict[str, UUID] = {}
        new: list[tuple[Entity, list[float]]] = []
        citations: list[tuple[UUID, ProvenanceRef]] = []
        for candidate, vector in zip(result.entities, vectors, strict=True):
            provenance = ref(candidate.excerpt, candidate.confidence)
            existing = self._match(candidate, vector)
            if existing:
                citations.append((existing, provenance))
                ids[name_key(candidate.name)] = existing
                continue
            entity = Entity(
                name=candidate.name, type=candidate.type, description=candidate.description,
                embedding=vector, confidence=candidate.confidence, provenance=[provenance],
            )
            new.append((entity, vector))
            ids[name_key(candidate.name)] = entity.id

        relationships = [
            Relationship(
                source_id=ids[name_key(r.source)], target_id=ids[name_key(r.target)],
                type=r.type, description=r.description, weight=r.confidence,
                provenance=[ref(r.excerpt, r.confidence)],
            )
            for r in result.relationships
            # Both names may have resolved to the same entity.
            if ids[name_key(r.source)] != ids[name_key(r.target)]
        ]
        try:
            self.store.write_extraction(
                unit.id, self.marker, [e for e, _ in new], citations, relationships
            )
        except DuplicateRecordError:
            return False
        if self._index is not None:
            for entity, vector in new:
                self._index.upsert(entity.id, "entity", vector)
        return True
