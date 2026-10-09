"""Entity merge candidates and merges.

A merge folds `entity_id` (the newer entity) into `target_id`: relationships,
claims, and citations move to the target, the source's names become aliases,
and the source is kept with status `merged` and `merged_into_id` as history.
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from neo4j import Driver, ManagedTransaction

from aether.core.models import MergeCandidate


class CandidateNotFoundError(LookupError):
    pass


class CandidateClosedError(ValueError):
    """Already decided, or one of its entities is no longer active."""


def _repoint(tx: ManagedTransaction, source: str, target: str) -> None:
    for edge, field in (("FROM", "source_id"), ("TO", "target_id")):
        tx.run(
            f"MATCH (s:Entity {{id: $source}})<-[old:{edge}]-(r:Relationship), "
            "(t:Entity {id: $target}) "
            f"CREATE (r)-[:{edge}]->(t) DELETE old SET r.{field} = $target",
            source=source, target=target,
        ).consume()
    for edge, field in (("SUBJECT", "subject_id"), ("OBJECT", "object_id")):
        tx.run(
            f"MATCH (s:Entity {{id: $source}})<-[old:{edge}]-(c:Claim), "
            "(t:Entity {id: $target}) "
            f"CREATE (c)-[:{edge}]->(t) DELETE old SET c.{field} = $target",
            source=source, target=target,
        ).consume()
    # A relationship between the two merged entities would now be a self-loop.
    tx.run(
        "MATCH (r:Relationship) WHERE r.source_id = $target AND r.target_id = $target "
        "SET r.status = 'deprecated'",
        target=target,
    ).consume()
    # Count the target's citations once, before any are added.
    tx.run(
        "MATCH (t:Entity {id: $target}) WITH t, COUNT { (t)-[:CITES]->() } AS base "
        "MATCH (:Entity {id: $source})-[c:CITES]->(u:TextUnit) "
        "CREATE (t)-[n:CITES]->(u) SET n = properties(c), n.position = base + c.position",
        source=source, target=target,
    ).consume()


class Neo4jMergeStore:
    def __init__(self, driver: Driver, database: str) -> None:
        self._driver = driver
        self._database = database

    def add_candidates(self, pairs: list[tuple[UUID, UUID, float]]) -> None:
        rows = [
            MergeCandidate(entity_id=e, target_id=t, similarity=round(sim, 4))
            .model_dump(mode="json")
            for e, t, sim in pairs
        ]
        self._driver.execute_query(
            "UNWIND $rows AS row "
            "MATCH (e:Entity {id: row.entity_id}), (t:Entity {id: row.target_id}) "
            "CREATE (m:MergeCandidate) SET m = row "
            "CREATE (m)-[:CANDIDATE]->(e) CREATE (m)-[:TARGET]->(t)",
            parameters_={"rows": rows}, database_=self._database,
        )

    def find(self, *, status: str | None = None, limit: int = 50) -> list[MergeCandidate]:
        records, _, _ = self._driver.execute_query(
            "MATCH (m:MergeCandidate) WHERE $status IS NULL OR m.status = $status "
            "RETURN properties(m) AS m ORDER BY m.similarity DESC, m.id LIMIT $limit",
            parameters_={"status": status, "limit": limit},
            database_=self._database, routing_="r",
        )
        return [MergeCandidate.model_validate(r["m"]) for r in records]

    def review(
        self, candidate_id: UUID, reviewer_id: UUID, decision: Literal["merge", "keep_separate"]
    ) -> MergeCandidate:
        def write(tx: ManagedTransaction) -> MergeCandidate:
            # Writing first takes the lock, so concurrent reviews serialize.
            record = tx.run(
                "MATCH (m:MergeCandidate {id: $id}) SET m.changed_at = $now "
                "WITH m MATCH (e:Entity {id: m.entity_id}), (t:Entity {id: m.target_id}) "
                "SET e.changed_at = $now, t.changed_at = $now "
                "RETURN properties(m) AS m, e.status AS e_status, t.status AS t_status, "
                "e.name AS e_name, e.aliases AS e_aliases, "
                "t.name AS t_name, t.aliases AS t_aliases",
                id=str(candidate_id), now=datetime.now(timezone.utc).isoformat(),
            ).single()
            if record is None:
                raise CandidateNotFoundError(f"Merge candidate {candidate_id} does not exist")
            statuses = {record["e_status"], record["t_status"]}
            if record["m"]["status"] != "open" or statuses != {"active"}:
                raise CandidateClosedError("Candidate is already decided or no longer active")
            source, target = record["m"]["entity_id"], record["m"]["target_id"]
            if decision == "merge":
                _repoint(tx, source, target)
                names = [record["e_name"], *(record["e_aliases"] or []),
                         *(record["t_aliases"] or [])]
                aliases = [n for n in dict.fromkeys(names) if n != record["t_name"]]
                tx.run(
                    "MATCH (s:Entity {id: $source}), (t:Entity {id: $target}) "
                    "SET s.status = 'merged', s.merged_into_id = $target, t.aliases = $aliases",
                    source=source, target=target, aliases=aliases,
                ).consume()
            status = "merged" if decision == "merge" else "kept_separate"
            updated = tx.run(
                "MATCH (m:MergeCandidate {id: $id}) SET m.status = $status, m.reviewed_by = $by "
                "RETURN properties(m) AS m",
                id=str(candidate_id), status=status, by=str(reviewer_id),
            ).single(strict=True)
            return MergeCandidate.model_validate(updated["m"])

        with self._driver.session(database=self._database) as session:
            return session.execute_write(write)
