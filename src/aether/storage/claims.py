"""Claim proposals, reviews, and disputes (ADR-0002).

Claims link to their author with PROPOSED_BY. Each decision is a Review node:
(Review)-[:REVIEWS]->(Claim) and (Review)-[:BY]->(Contributor). Every excerpt
must appear in its cited text unit. Reputation is counted on read.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal, NamedTuple
from uuid import UUID

from neo4j import Driver, ManagedTransaction
from neo4j.exceptions import ConstraintError

from aether.core.models import Claim, EvidenceRef, Review
from aether.core.text import name_key
from aether.storage.knowledge import (
    DuplicateRecordError,
    Row,
    _load,
    _write,
    claim_node,
)

REVIEWABLE = ("proposed", "under_review")
DISPUTABLE = ("proposed", "under_review", "verified")
MIN_EXCERPT_CHARS = 3


class ClaimNotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    """The claim's current status does not allow this action."""


class SelfReviewError(PermissionError):
    pass


class InvalidEvidenceError(ValueError):
    """A cited text unit is missing or does not contain the excerpt."""


class ClaimRecord(NamedTuple):
    claim: Claim
    author_id: UUID | None
    reviews: list[Review]


def _check_evidence(tx: ManagedTransaction, refs: list[EvidenceRef]) -> None:
    texts = {
        row["id"]: row["text"]
        for row in tx.run(
            "MATCH (t:TextUnit) WHERE t.id IN $ids RETURN t.id AS id, t.text AS text",
            ids=[str(ref.text_unit_id) for ref in refs],
        ).data()
    }
    for ref in refs:
        text = texts.get(str(ref.text_unit_id))
        if text is None:
            raise InvalidEvidenceError(f"Text unit {ref.text_unit_id} does not exist")
        excerpt = name_key(ref.excerpt or "")
        # An empty key matches any text. Substring matching stays loose beyond
        # this floor; reviewers see the excerpt.
        if len(excerpt) < MIN_EXCERPT_CHARS:
            raise InvalidEvidenceError("Excerpts need at least 3 non-space characters")
        if excerpt not in name_key(text):
            raise InvalidEvidenceError(f"Excerpt not found in text unit {ref.text_unit_id}")


def _lock(tx: ManagedTransaction, claim_id: UUID, allowed: tuple[str, ...]) -> Row:
    # Writing first takes the node's write lock, so concurrent decisions on one
    # claim serialize and each reads the status the previous one committed.
    record = tx.run(
        "MATCH (c:Claim {id: $id}) SET c.changed_at = $now "
        "WITH c OPTIONAL MATCH (c)-[:PROPOSED_BY]->(a:Contributor) "
        "RETURN c.status AS status, a.id AS author, "
        "COUNT { (c)-[:CITES {supports: false}]->() } AS counter",
        id=str(claim_id), now=datetime.now(timezone.utc).isoformat(),
    ).single()
    if record is None:
        raise ClaimNotFoundError(f"Claim {claim_id} does not exist")
    if record["status"] not in allowed:
        raise InvalidTransitionError(f"Claim is {record['status']}")
    return record.data()


def _record_review(tx: ManagedTransaction, review: Review) -> None:
    tx.run(
        "MATCH (c:Claim {id: $claim}), (p:Contributor {id: $by}) "
        "CREATE (r:Review) SET r = $props CREATE (r)-[:REVIEWS]->(c) CREATE (r)-[:BY]->(p)",
        claim=str(review.claim_id), by=str(review.contributor_id),
        props=review.model_dump(mode="json"),
    ).consume()


class Neo4jClaimStore:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def _write(self, work: Callable[[ManagedTransaction], None]) -> None:
        with self._driver.session(database=self._database) as session:
            session.execute_write(work)

    def propose(self, claim: Claim, author_id: UUID) -> None:
        if claim.status != "proposed" or claim.verified_at or claim.verified_by:
            raise ValueError("New claims must be unverified proposals")
        node, refs, links = claim_node(claim)
        links.append(("PROPOSED_BY", "Contributor", str(author_id)))

        def write(tx: ManagedTransaction) -> None:
            _check_evidence(tx, [*claim.evidence, *claim.counter_evidence])
            _write(tx, "Claim", node, refs, links)

        try:
            self._write(write)
        except ConstraintError as error:
            raise DuplicateRecordError(f"Claim {claim.id} already exists") from error

    def review(
        self, claim_id: UUID, reviewer_id: UUID, decision: Literal["accept", "reject"],
        notes: str,
    ) -> None:
        def write(tx: ManagedTransaction) -> None:
            current = _lock(tx, claim_id, REVIEWABLE)
            if current["author"] == str(reviewer_id):
                raise SelfReviewError("Authors cannot review their own claims")
            accepted = decision == "accept"
            tx.run(
                "MATCH (c:Claim {id: $id}) SET c.status = $status, c.polarity = $polarity, "
                "c.verified_at = $at, c.verified_by = $by",
                id=str(claim_id),
                status="verified" if accepted else "rejected",
                polarity=("mixed" if current["counter"] else "supported") if accepted
                else "uncertain",
                at=datetime.now(timezone.utc).isoformat() if accepted else None,
                by=str(reviewer_id) if accepted else None,
            ).consume()
            _record_review(tx, Review(
                claim_id=claim_id, contributor_id=reviewer_id, kind=decision, notes=notes,
            ))

        self._write(write)

    def dispute(
        self, claim_id: UUID, contributor_id: UUID, reason: str, counter: list[EvidenceRef]
    ) -> None:
        if not counter or any(ref.supports for ref in counter):
            raise InvalidEvidenceError("A dispute needs opposing evidence")

        def write(tx: ManagedTransaction) -> None:
            _lock(tx, claim_id, DISPUTABLE)
            _check_evidence(tx, counter)
            for ref in counter:
                edge = ref.model_dump(mode="json")
                unit = edge.pop("text_unit_id")
                tx.run(
                    "MATCH (c:Claim {id: $id}), (t:TextUnit {id: $unit}) "
                    "WITH c, t, COUNT { (c)-[:CITES]->() } AS position "
                    "CREATE (c)-[e:CITES]->(t) SET e = $edge, e.position = position",
                    id=str(claim_id), unit=unit, edge=edge,
                ).consume()
            tx.run(
                "MATCH (c:Claim {id: $id}) SET c.status = 'under_review', "
                "c.polarity = 'disputed', c.verified_at = null, c.verified_by = null",
                id=str(claim_id),
            ).consume()
            _record_review(tx, Review(
                claim_id=claim_id, contributor_id=contributor_id, kind="dispute", notes=reason,
            ))

        self._write(write)

    def _load_claims(self, ids: list[str]) -> list[Claim]:
        records, _, _ = self._driver.execute_query(
            "MATCH (n:Claim) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[c:CITES]->(t:TextUnit) "
            "WITH n, c, t ORDER BY c.position "
            "RETURN properties(n) AS data, collect(c {.*, text_unit_id: t.id}) AS refs",
            parameters_={"ids": ids}, database_=self._database, routing_="r",
        )
        claims = {}
        for record in records:
            refs = [{k: v for k, v in r.items() if k != "position"} for r in record["refs"]]
            claims[record["data"]["id"]] = _load(Claim, record["data"], ("contributors",), {
                "evidence": [r for r in refs if r["supports"]],
                "counter_evidence": [r for r in refs if not r["supports"]],
            })
        return [claims[i] for i in ids if i in claims]

    def get(self, claim_id: UUID) -> ClaimRecord | None:
        claims = self._load_claims([str(claim_id)])
        if not claims:
            return None
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Claim {id: $id}) OPTIONAL MATCH (c)-[:PROPOSED_BY]->(a:Contributor) "
            "OPTIONAL MATCH (r:Review)-[:REVIEWS]->(c) WITH a, r ORDER BY r.created_at "
            "RETURN a.id AS author, collect(properties(r)) AS reviews",
            parameters_={"id": str(claim_id)}, database_=self._database, routing_="r",
        )
        author = records[0]["author"]
        return ClaimRecord(
            claims[0], UUID(author) if author else None,
            [Review.model_validate(r) for r in records[0]["reviews"]],
        )

    def find(
        self, *, status: str | None = None, entity_id: UUID | None = None, limit: int = 50
    ) -> list[Claim]:
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Claim) WHERE ($status IS NULL OR c.status = $status) "
            "AND ($entity IS NULL OR EXISTS { (c)-[:SUBJECT|OBJECT]->(:Entity {id: $entity}) }) "
            "RETURN c.id AS id ORDER BY c.created_at DESC, c.id LIMIT $limit",
            parameters_={"status": status, "limit": limit,
                         "entity": str(entity_id) if entity_id else None},
            database_=self._database, routing_="r",
        )
        return self._load_claims([r["id"] for r in records])

    def verified_about(self, entity_ids: list[UUID], *, limit: int = 10) -> list[Claim]:
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Claim {status: 'verified'})-[:SUBJECT|OBJECT]->(e:Entity) "
            "WHERE e.id IN $ids RETURN DISTINCT c.id AS id, c.confidence AS confidence "
            "ORDER BY confidence DESC, id LIMIT $limit",
            parameters_={"ids": [str(i) for i in entity_ids], "limit": limit},
            database_=self._database, routing_="r",
        )
        return self._load_claims([r["id"] for r in records])

    def reputation(self, contributor_id: UUID) -> tuple[int, int]:
        """(accepted, rejected) claims proposed by this contributor."""
        records, _, _ = self._driver.execute_query(
            "MATCH (c:Claim)-[:PROPOSED_BY]->(:Contributor {id: $id}) "
            "RETURN count(CASE WHEN c.status = 'verified' THEN 1 END) AS accepted, "
            "count(CASE WHEN c.status = 'rejected' THEN 1 END) AS rejected",
            parameters_={"id": str(contributor_id)}, database_=self._database, routing_="r",
        )
        return records[0]["accepted"], records[0]["rejected"]
