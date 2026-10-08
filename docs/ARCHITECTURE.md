# Aether architecture

This is the current implementation architecture. It revises the supplied ZIP
documentation's Kuzu storage choice under [ADR-0001](adr/0001-use-neo4j.md).
The original archives remain historical references for the product vision and
domain schema; where their storage guidance conflicts, this document takes
precedence.

## Current implementation

Implemented: Nix development shell, direnv setup, Python package, Pydantic domain
models, model tests, and the [local Neo4j runtime](LOCAL_DATABASE.md). The runtime
has passed authenticated connectivity and a database-restart durability check.

Not implemented: Aether model persistence, LanceDB integration, FastAPI, ingestion,
extraction, retrieval, or contribution review workflows. Model validation does
not prove that referenced records exist or authorize a claim's verification.

## Foundation design

```text
CLI / future HTTP client
          |
          v
One Python application
  FastAPI routes -> application services -> Pydantic domain models
                          |
                  persistence module
                          |
                          v
                Local Neo4j instance
                records + evidence links

Application -> local files (original documents)
Application -> LanceDB (derived embedding index; later foundation step)
```

| Concern | Choice |
| --- | --- |
| Development tools | Nix flake, Python 3.12, direnv, virtual environment |
| Domain validation | Pydantic v2 |
| Graph storage | Local Neo4j Community; official Python driver |
| Original documents | Local filesystem initially |
| Embedding index | LanceDB, introduced after graph persistence |
| API | FastAPI, introduced after basic persistence |
| LLM calls | Deferred until extraction; retain the planned provider abstraction |

Use modules inside one application. A separate gateway, queue service, worker
fleet, and orchestration platform are outside foundation scope.

## Graph records and evidence

- `TextUnit`, `Entity`, and `Claim` are nodes keyed by stable application UUIDs.
- A relationship connects its source entity to its target entity and preserves
  its own ID, type, properties, and provenance. The persistence design must
  support distinct evidenced relationships between the same two entities.
- Entity provenance links to stored text units; claims link to supporting and
  opposing evidence. Preserve excerpts, attribution, and timestamps.
- The repository must validate referenced records and write a record plus its
  evidence links in one transaction. Missing references must not leave partial
  writes.
- Names are searchable labels, not identity keys: different entities can share
  a name. This corrects the original schema's suggested unique-name index.
- A content hash identifies matching text, not a unique source occurrence.
  Equal text from different documents must retain both provenance paths. Do
  not apply the original global unique constraint on `TextUnit.content_hash`.
- Updates must preserve evidence integrity. Reject deletion of referenced text
  units; use explicit deprecation/supersession for knowledge lifecycle changes.
- A model's `verified` value does not authorize a write. Verified-graph changes
  must eventually pass the documented contribution policy. The first storage
  milestone handles text units only.

The name/hash indexing corrections above are implementation decisions in this
revision, rather than claims that the supplied docs already specify them.

## Storage boundary

Place Neo4j access in `src/aether/storage/`. API routes and domain models must
not contain database queries. Use parameterized queries and explicit mappings
between database records and models. Validate models on read as well as write.

Keep graph records authoritative for structured knowledge. Treat vectors as a
rebuildable index keyed by source ID and embedding model/version. A failed index
write must remain retryable; do not report indexing complete until it succeeds.

## Local operation

Neo4j runs separately from the Python application. Bind the development database
to loopback and keep authentication enabled. Read connection configuration from
`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE`; commit only
an example without real credentials when runtime setup is implemented.

Keep database files, documents, and backups in ignored local directories.
Document explicit start, stop, connectivity, and restore commands after testing
the selected runtime. The existing `.envrc` loads the Python and Neo4j tools; it
neither launches Neo4j nor automatically loads an `.env` file. The local runtime
helper reads `.env` explicitly.

## Acceptance evidence

The next milestone must save a text unit, close and reopen the connection,
restart Neo4j, and retrieve the same validated record. Later milestones add
transaction rollback and reference-integrity tests for evidence-bearing records.

Foundation completion also requires the API to accept a document and list its
persisted text units. Passing model tests alone does not establish these results.
