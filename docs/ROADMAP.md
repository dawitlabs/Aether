# Aether foundation roadmap

This updates Phase 0 of the supplied roadmap to use Neo4j under
[ADR-0001](adr/0001-use-neo4j.md). Work proceeds one milestone at a time; the
later extraction, GraphRAG, contribution, and governance phases remain future
work. These are completion gates, not calendar estimates.

## Completed locally

- [x] Git repository, MIT license, and contribution guide.
- [x] Nix Python shell and direnv integration.
- [x] Python package configuration and development dependencies.
- [x] Pydantic `TextUnit`, `Entity`, `Relationship`, and `Claim` models.
- [x] Supporting provenance/evidence references and model tests.

Some completed files remain uncommitted. The models use a minimal
`ContributorRef.contributor_id` because the supplied schema leaves that shape
undefined. They do not implement storage integrity or verification governance.

## 1. Local Neo4j runtime

- [x] Validate and pin compatible server, Java, and Python driver versions.
- [x] Add reproducible development tooling and explicit start/stop instructions.
- [x] Configure loopback binding, authentication, ignored data, and credential examples.
- [x] Verify connectivity and persistence across a database restart.

Exit: a reproducible local database starts and stops with documented commands.

Verified locally on 2026-10-08: Neo4j Community 2026.09.0, Nix-provided Java
21.0.12.1, and Python driver 6.4.0. Invalid credentials were rejected and a probe
record survived a full database restart. Both TCP listeners were loopback-only.
The instance was stopped cleanly after verification. See [runtime guide](LOCAL_DATABASE.md).

## 2. Graph schema and persistence

- [x] Add versioned, repeatable schema initialization and UUID constraints.
- [x] Implement text-unit create, read/list, update, and guarded delete first.
- [x] Verify complete round trips, duplicate-ID handling, and restart durability.
- [x] Add entities, relationships, and claims with transactional evidence links.
- [x] Reject missing references and prove rollback on failed writes.
- [x] Preserve distinct source occurrences when text hashes match.
- [x] Document and exercise local backup/restore before relying on stored data.

Exit: basic operations work against real Neo4j and preserve provenance.

## 3. LanceDB skeleton

- [x] Introduce the embedding-store module. The caller passes the index path;
  application configuration arrives with the FastAPI skeleton.
- [x] Test vector upsert/read using fixed test vectors and source IDs.
- [x] Validate model identity/dimensions and define retry/rebuild behavior.

Exit: an index round trip works without requiring an LLM or extraction pipeline.

## 4. FastAPI skeleton

- [x] Add `/health` and generated OpenAPI.
- [x] Manage database connections through application startup/shutdown.
- [x] Distinguish process health from database readiness.
- [x] Test the API lifecycle and database-unavailable behavior.

## 5. Minimal document ingestion

- [ ] Define document identity and source metadata before accepting uploads.
- [ ] Begin with UTF-8 text uploads and explicit size limits.
- [ ] Store originals and create traceable text units with hashes and token counts.
- [ ] Define consistent token counting and retry/idempotency behavior.
- [ ] Expose document lookup and text-unit listing through the API.
- [ ] Verify upload, restart, and retrieval through the running application.

Exit: enter the development environment, start Neo4j and the API, upload a
document, and retrieve its persisted text units. PDF parsing, advanced chunking,
LLM extraction, and public deployment are outside this foundation milestone.
