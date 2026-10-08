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

- [x] Define document identity and source metadata before accepting uploads.
- [x] Begin with UTF-8 text uploads and explicit size limits.
- [x] Store originals and create traceable text units with hashes and token counts.
- [x] Define consistent token counting and retry/idempotency behavior.
- [x] Expose document lookup and text-unit listing through the API.
- [x] Verify upload, restart, and retrieval through the running application.

Verified 2026-10-08 against Uvicorn on 127.0.0.1:8000: upload returned 201, re-upload
200; `/ready` returned 503 while Neo4j was stopped; text units were identical
after the restart and matched the file's offsets.

Exit: enter the development environment, start Neo4j and the API, upload a
document, and retrieve its persisted text units. PDF parsing, advanced chunking,
LLM extraction, and public deployment are outside this foundation milestone.

# Phase 1 — Extraction MVP

Goal: turn stored text units into a queryable graph with provenance.

LLM decision: everything must run at zero cost. Chat defaults to
`gpt-oss:120b-cloud` through Ollama's free tier (verified 2026-10-08; most
other cloud models return HTTP 402 without credits), with a small local model
as fallback. Embeddings use local `nomic-embed-text`. The
client speaks the OpenAI-compatible API, so switching provider is
configuration only (`AETHER_LLM_BASE_URL`, `AETHER_LLM_MODEL`,
`AETHER_LLM_API_KEY`).

Deviations from the supplied roadmap: no LiteLLM (every free option is
OpenAI-compatible), no Redis or worker queue (per-unit extraction markers make
re-runs idempotent), chunking reuses `split_text`, and claim extraction stays
in Phase 3.

## 1. LLM client

- [x] Chat requests with JSON output, timeout, and backoff on 429/5xx.
- [x] Embedding requests through the same client.
- [ ] Unit tests use a fake; one live call against Ollama returns valid JSON.

## 2. Versioned prompt and parser

- [x] Prompt stored in the repo with a version recorded in provenance.
- [x] Parse candidate entities and relationships.
- [x] Reject excerpts absent from the text unit and relationships naming
  unextracted entities.
- [x] Tests cover good and malformed canned output.

## 3. Entity resolution

- [ ] Match on normalized name plus type, then embedding similarity.
- [ ] Matches append provenance to the existing entity.
- [ ] The same entity extracted from two units yields one entity, two citations.

Merging already-stored entities and a review queue are Phase 4 work.

## 4. Graph write

- [ ] One transaction per text unit: entities, relationships, and an
  extraction marker (prompt version + model).
- [ ] Re-runs skip finished units; failed writes leave nothing partial.

## 5. Extraction job

- [ ] `POST /documents/{id}/extraction` returns 202; `GET` reports progress.
- [ ] Interrupting and re-running completes without duplicates.

## 6. Local query

- [ ] `GET /entities?name=` and `GET /entities/{id}/neighborhood` (one hop plus
  citing text units).
- [ ] `POST /query`: embed question, nearest entities, neighborhoods, LLM answer
  citing text-unit IDs.

Exit: upload 3–5 small documents, extract them, query a neighborhood, and get
an answer with real citations.
