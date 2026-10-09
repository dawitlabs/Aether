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
as fallback. Embeddings use local `all-minilm` (46 MB; `nomic-embed-text`
would not download on the development connection). The
client speaks the OpenAI-compatible API, so switching provider is
configuration only (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, and
`EMBED_*` equivalents).

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

- [x] Match on normalized name plus type, then embedding similarity.
- [x] Matches append provenance to the existing entity.
- [x] The same entity extracted from two units yields one entity, two citations.

Merging already-stored entities and a review queue are Phase 4 work. The
0.95 cosine merge threshold is checked on six pairs only, not calibrated; integration tests use a fake embedder.

## 4. Graph write

- [x] One transaction per text unit: entities, relationships, and an
  extraction marker (prompt version + model).
- [x] Re-runs skip finished units; failed writes leave nothing partial.

## 5. Extraction job

- [x] `POST /documents/{id}/extraction` returns 202; `GET` reports progress.
- [x] Interrupting and re-running completes without duplicates.

## 6. Local query

- [x] `GET /entities?name=` and `GET /entities/{id}/neighborhood` (one hop plus
  citing text units).
- [x] `POST /query`: embed question, nearest entities, neighborhoods, LLM answer
  citing text-unit IDs.

Exit: upload 3–5 small documents, extract them, query a neighborhood, and get
an answer with real citations.

Verified 2026-10-08 against Uvicorn on 127.0.0.1:8000 with `gpt-oss:120b-cloud`
and `all-minilm`: four related documents extracted to `complete`; shared
entities (Paris, Radium, Irène Joliot-Curie) resolved to one node each with two
citations; three factual questions returned correct answers citing one text
unit each; an off-topic question returned no answer and no citations;
re-queuing a finished document added nothing.

Known limits: extraction recall varies between runs (one run omitted Marie
and Pierre Curie from a passage naming them); a second "gleaning" pass would
help. Embeddings cannot tell name variants ("Marie Skłodowska-Curie") from
different people ("Pierre Curie"); both stay separate entities.

# Phase 2 — GraphRAG features

Goal: community summaries and thematic (global) questions with grounded
citations, measured by a repeatable evaluation.

Dependency: `networkx` 3.7 for its pure-Python Leiden implementation (no C
extensions). Single-level communities only until the graph needs hierarchy.

## 1. LLM response cache

- [x] Cache `chat_json` results on disk, keyed by model, prompt, and input.
- [x] Cache hits make no provider call; corrupt entries are ignored.

## 2. Communities

- [x] Leiden over active entities, relationship-weighted, fixed seed.
- [x] A rebuild replaces all communities in one transaction.
- [x] `POST /communities/rebuild` runs on the extraction worker.

## 3. Community reports

- [x] One report per community: title, summary, findings.
- [x] Findings cite only text units that member entities cite.
- [x] Report summaries are embedded for retrieval.

## 4. Query modes and citations

- [x] `POST /query` accepts `mode`: `local`, `global`, or `hybrid`.
- [x] Citations carry text unit, document, and a quote verified verbatim.

## 5. Evaluation harness

- [x] A committed corpus and golden questions (factual, thematic, unanswerable).
- [x] `scripts/eval.py` logs keyword recall, citation precision, and abstention.

Exit: ingest the corpus, rebuild communities, answer local and thematic
questions with citations, and log evaluation scores.

Verified 2026-10-09 with `gpt-oss:120b-cloud` and `all-minilm`: `python
scripts/eval.py` ingested and extracted the four-document corpus, rebuilt four
communities with reports, and scored 11 golden questions (5 local, 4 hybrid,
2 global): keyword recall 1.0, citation precision 1.0, abstention accuracy
1.0. The first run scored abstention 0.909: a correct citation was rejected
because the model wrote U+2011 for a hyphen; `name_key` now folds Unicode
dashes and curly quotes.

These scores are saturated on a tiny, easy corpus. They prove the harness,
not answer quality; a larger corpus with harder questions is needed before
scores can guide changes.

# Phase 3 — Claims & Contribution

Goal: humans and agents propose evidence-backed claims; humans review them;
verified claims inform answers. Policy: [ADR-0002](adr/0002-contribution-policy.md).

## 1. Identity

- [x] Contributor records with hashed API keys and permissions.
- [x] A local script creates the first admin; admins create contributors.
- [x] Every write endpoint requires a valid key; reads stay open.

## 2. Proposals

- [x] `POST /claims` with mandatory, verbatim-checked evidence.
- [x] Claims record their author and start as `proposed`.

## 3. Review

- [x] `POST /claims/{id}/review` accepts or rejects, recorded as a Review.
- [x] Only humans with `review`, never the author.

## 4. Disputes

- [x] `POST /claims/{id}/dispute` with counter-evidence reopens review.

## 5. Reputation

- [x] Accepted and rejected claim counts per contributor.

## 6. Query integration

- [x] Verified claims about matched entities join the query context; answers
  return `claim_ids`.

Exit: an agent proposes a claim with evidence, a human accepts it, and a query
uses it.

Verified 2026-10-09 through the real API in-process (no port) with
`gpt-oss:120b-cloud`: an agent proposed a claim about Marie Curie with a
verbatim excerpt (201, `proposed`); the agent's review attempt got 403; a
human reviewer accepted it (`verified`, `supported`); a local query answered
from it, returned its ID in `claim_ids`, and cited one passage; the agent's
profile showed one accepted claim. The check's contributors and claim were
deleted afterwards.

# Phase 4 — Agent readiness & hardening

Goal: an external agent can be registered, propose, and query reliably.
Phase 5 in the supplied roadmap is ongoing governance work, not milestones.

## 1. Stable API contract

- [x] All routes under `/api/v0`; `/health` and `/ready` stay at the root for probes.
- [x] `GET /api/v0/version`; errors use `{"error": ..., "detail": ...}`.
- [x] A committed OpenAPI snapshot fails tests on unplanned contract changes.

## 2. Rate limiting & auth hardening

- [x] Per-contributor (or per-IP without a key) limits; stricter on `/query`.
- [x] 429 responses carry `Retry-After`.
- [x] Contributors rotate their own key; admins revoke keys over the API.

## 3. Entity resolution review

- [x] Similar-but-uncertain entity pairs are queued as merge candidates.
- [x] Human reviewers merge or keep them separate; merges keep history.

## 4. Observability

- [ ] Structured JSON logs with request IDs and timing; no PII or keys.
- [ ] `GET /api/v0/admin/stats` for graph counts and request metrics.

## 5. Agent integrator docs

- [ ] `docs/AGENTS.md` and a tested, dependency-free example client.

## 6. First domain pack

- [ ] A domain pack format (corpus, golden questions, sources).
- [ ] A first pack, the evaluation run against it, and an in-process demo.

Exit: an agent registered by an admin proposes a claim, a human accepts it,
and the agent queries it, all through `/api/v0`; the domain pack evaluates.
