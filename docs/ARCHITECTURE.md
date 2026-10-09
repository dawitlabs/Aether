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

Implemented storage: create/read for all four models; text-unit update and
guarded delete. LanceDB embedding index (`storage/vectors.py`). Not implemented:
entity/relationship/claim updates and deletes, non-text ingestion,
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
Application -> LanceDB (derived embedding index; .local/lancedb)
```

| Concern | Choice |
| --- | --- |
| Development tools | Nix flake, Python 3.12, direnv, virtual environment |
| Domain validation | Pydantic v2 |
| Graph storage | Local Neo4j Community; official Python driver |
| Original documents | Local filesystem initially |
| Embedding index | LanceDB 0.40.0, one table per model and dimension count |
| API | FastAPI 0.143.0 on Uvicorn 0.54.0 (`src/aether/api/app.py`) |
| LLM calls | Stdlib client for OpenAI-compatible endpoints (`extraction/llm.py`); default Ollama |

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
  must eventually pass the documented contribution policy. Until then, the
  store rejects claims marked verified.
- Relationships are stored as `Relationship` nodes with `FROM`/`TO` edges to
  entities, so each can cite its own text units. All citations use `CITES`
  edges ordered by `position`; claim citations carry `supports`.

The name/hash indexing corrections above are implementation decisions in this
revision, rather than claims that the supplied docs already specify them.

## Storage boundary

Place Neo4j access in `src/aether/storage/`. API routes and domain models must
not contain database queries. Use parameterized queries and explicit mappings
between database records and models. Validate models on read as well as write.

Keep graph records authoritative for structured knowledge. Treat vectors as a
rebuildable index keyed by source ID and embedding model/version. A failed index
write must remain retryable; do not report indexing complete until it succeeds.

Vector index rules: writes are upserts keyed by source ID, so retries are
safe. Vectors must match the table's dimension count and be finite. To rebuild,
delete the index directory and re-upsert from the embeddings stored in Neo4j.
The dev shell sets `LD_LIBRARY_PATH` to Nix's C++ runtime because the prebuilt
numpy, pyarrow, and lancedb wheels need it.

API rules: `/health` reports process liveness and never touches the database.
`/ready` runs one authenticated query without retries and returns 503 when
Neo4j is unreachable, without exposing error details. The driver opens at
startup and closes at shutdown; the API starts even while Neo4j is down.

Ingestion rules (`src/aether/ingestion.py`):

- `POST /documents` takes a raw `text/plain; charset=utf-8` body up to 1 MiB.
  Identical bytes map to one document (unique `content_hash`), so re-uploads
  and retries return the original with 200 instead of creating duplicates.
- Originals are stored content-addressed at `.local/documents/<sha256>.txt`
  before the database write, so a failed write can simply be retried.
- Text is split into units of at most 2,000 characters, breaking at blank
  lines, then line breaks, then spaces. Offsets are character positions in the
  decoded text; each unit's text is exactly `text[start_offset:end_offset]`.
- `token_count` counts whitespace-separated words until a model tokenizer is
  chosen. Whitespace-only spans are skipped.
- The document and all of its text units are written in one transaction.
- Database failures return 503 without details. The driver retries transient
  errors for at most 3 seconds per query.
- The API has no authentication. It must stay bound to loopback until auth exists.

Extraction rules (`src/aether/extraction/`):

- `extract.py` holds the versioned prompt. Model output is untrusted: items
  whose excerpt is not in the text unit, and relationships whose ends were not
  extracted, are dropped.
- `pipeline.py` resolves each entity to an existing active entity of the same
  type by exact name key, then by embedding cosine >= 0.95; otherwise it creates
  one. A match appends a `CITES` edge to the existing entity.
- One transaction per text unit writes new entities, citations, relationships,
  and an `:Extraction {key: "<unit id>|<prompt version>/<model>"}` marker. Its
  unique constraint makes re-runs skip finished units.
- Units are processed sequentially; concurrent runs may duplicate entities.
- Entity vectors are upserted to LanceDB after commit, unit-normalized.
- `POST /documents/{id}/extraction` queues the document on a single worker
  thread and returns 202; `GET` reports `not_started`, `running`, `failed`, or
  `complete` with extracted/total counts from the markers. Provider errors stop
  the job; a malformed response skips one unit. Shutdown abandons the running
  unit, which a re-run redoes.

Query rules (`src/aether/storage/graph.py`, `src/aether/query.py`):

- `GET /entities?name=` matches name keys by substring; `GET
  /entities/{id}/neighborhood` returns one hop plus every cited text unit.
  Responses never include embeddings.
- `POST /query` takes `mode`. `local`: the 3 nearest entities' neighborhoods.
  `global`: the 3 nearest community reports as background plus the text units
  their findings cite. `hybrid`: both, local units first. At most 8 units.
- Only text units are citable. Each citation is `{text_unit_id, document_id,
  quote}`; it is dropped unless the unit was supplied and the quote appears in
  it verbatim, ignoring case and whitespace. With no context the model is not
  called.
- Integration tests rebuild communities across the whole dev database; rerun
  a real rebuild afterwards (cached reports make it cheap).
- Provider failures return 502 without details.

Community rules (`src/aether/communities/`, `storage/communities.py`):

- Leiden (networkx, modularity metric, seed 42) over active entities;
  parallel relationship weights add. Single level; singletons are dropped.
- `POST /communities/rebuild` queues on the extraction worker and replaces
  every `:Community` node and `IN_COMMUNITY` edge in one transaction.
  Community IDs change on each rebuild.
- Each rebuild then writes one report per community (`communities/reports.py`):
  title, summary, and findings. Context is the members, relationships among
  them, and their 12 most-cited text units. Findings citing no supplied unit
  are dropped; the summary itself is not citation-checked.
- Report summaries are embedded as `community` vectors, replacing all previous
  ones. Vector search filters by kind before applying its limit.

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
