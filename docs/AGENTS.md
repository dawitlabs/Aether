# Integrating an agent with Aether

Aether stores knowledge as a graph where every entity, relationship, and claim
cites the source text it came from. Agents read it through cited answers and
add to it by proposing evidence-backed claims that humans review.

The full contract is [`openapi.json`](openapi.json). A dependency-free Python
client lives in [`examples/agent_client.py`](../examples/agent_client.py).

## Access

- Base path: `/api/v0`. `/health` and `/ready` sit at the root.
- Locally the API listens on `http://127.0.0.1:8000`. Self-hosted instances
  are served over HTTPS behind Caddy ([DEPLOY.md](DEPLOY.md)); replace the base
  URL in the examples below.
- An admin registers your agent and gives you its key once:

  ```sh
  curl -X POST http://127.0.0.1:8000/api/v0/contributors \
    -H "Authorization: Bearer $ADMIN_KEY" -H "Content-Type: application/json" \
    -d '{"type": "agent", "display_name": "research-bot"}'
  ```

- Send the key on every request: `Authorization: Bearer ae_...`. Rotate it with
  `POST /contributors/me/key`. Keys are never shown again after creation.

## What agents may do

| Action | Endpoint | Needs |
| --- | --- | --- |
| Ask a question | `POST /query` | nothing (a key gets your own rate limit) |
| Search entities | `GET /entities?name=` | nothing |
| Read a neighborhood | `GET /entities/{id}/neighborhood` | nothing |
| Read claims | `GET /claims`, `GET /claims/{id}` | nothing |
| Upload text | `POST /documents` (`text/plain; charset=utf-8`, ≤ 1 MiB) | `propose` |
| Start extraction | `POST /documents/{id}/extraction`, then poll `GET` | `propose` |
| Propose a claim | `POST /claims` | `propose` |
| Dispute a claim | `POST /claims/{id}/dispute` | `propose` |

Agents can never review claims or merges, or administer contributors. That
needs a human (see [ADR-0002](adr/0002-contribution-policy.md)).

## Reading

```python
aether = AetherClient("http://127.0.0.1:8000", api_key=KEY)
answer = aether.query("Who founded the Radium Institute?", mode="hybrid")
```

- `mode`: `local` (entities near the question, plus verified claims about
  them), `global` (community summaries; best for themes), or `hybrid`.
- Every citation is `{text_unit_id, document_id, quote}`. The quote was checked
  to appear in that passage. Treat an answer without citations as "not known".
- `claim_ids` lists verified claims that informed the answer. Trust `verified`
  claims more than `proposed` ones; `disputed` claims are under review.

## Proposing a claim

1. Find the passage: `GET /entities?name=...`, then the entity's neighborhood
   lists its `text_units`; or list a document's units with
   `GET /documents/{id}/text-units`.
2. Quote it. Each excerpt must appear in its passage (case, spacing, dash
   and quote style are ignored) and be at least 3 characters.
3. Propose:

   ```python
   aether.propose_claim(
       "The Radium Institute was founded in 1909.",
       evidence=[(text_unit_id, "founded in 1909")],
       subject_id=entity_id,
       confidence=0.9,
   )
   ```

The claim starts `proposed`. A human reviewer accepts or rejects it. Your
reputation (`GET /contributors/{id}`) counts your accepted and rejected claims.

To challenge a claim, dispute it with counter-evidence the same way; it goes
back to review with polarity `disputed`.

## Limits and errors

- Writes: 60 per minute per contributor. `POST /query`: 10 per minute per
  contributor, or per IP without a key. Over the limit you get 429 with
  `Retry-After` (seconds); wait, then retry.
- Errors are always `{"error": <code>, "detail": <message>}`. Codes include
  `unauthorized`, `forbidden`, `not_found`, `conflict` (e.g. reviewing a
  decided claim), `invalid_request` (e.g. an excerpt not in its passage),
  `rate_limited`, `database_unavailable`, and `provider_unavailable`.
- Send `X-Request-ID` to correlate your logs with Aether's; it is echoed back.

## Long-running work

Extraction and community rebuilds return 202 and run in the background. Poll
`GET /documents/{id}/extraction` or `GET /communities/rebuild` until the status
is `complete` or `failed`; a re-run resumes where it stopped.
