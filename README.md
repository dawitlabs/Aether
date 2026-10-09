# Aether

An evidence-backed collective knowledge engine. Aether turns documents into a
knowledge graph where every entity, relationship, and claim cites the passage
it came from. People and AI agents query it for cited answers and add to it by
proposing claims that humans review.

- **Cited answers.** Every citation is checked: its quote must appear verbatim
  in the passage it names, or it is dropped. With no support, Aether abstains.
- **GraphRAG.** Local questions use entity neighbourhoods; global questions use
  community reports built over the whole graph; hybrid uses both.
- **Contribution with review.** Agents propose claims with evidence; humans
  accept or reject them; accepted claims feed later answers.
- **Agent-ready API.** Versioned HTTP API (`/api/v0`) with per-contributor keys,
  rate limits, and a committed OpenAPI contract.
- **Any OpenAI-compatible model.** Defaults to Ollama; runs locally.

## Quickstart

Needs git and Docker (Linux, macOS, or Windows with WSL2).

```sh
git clone https://github.com/dawitlabs/Aether.git && cd Aether
./aether onboard
```

`onboard` asks which chat model to use (Ollama's free cloud model or any
OpenAI-compatible provider), starts Neo4j, Ollama, and the API, downloads the
embedding model, walks you through the Ollama sign-in, and prints your admin
key once. Then:

```sh
./aether demo    # the full agent workflow on the sample radioactivity pack
./aether mcp     # the command that connects Claude Code or another MCP client
```

> `./aether` and the Docker setup are new: Neo4j, the API, admin keys, and
> uploads are tested; the Ollama container steps are not yet. Please
> [open an issue](https://github.com/dawitlabs/Aether/issues) if one fails.

## Command reference

Run from the repository folder. `$KEY` is an API key (`ae_...`); `$ID` is an ID
returned by an earlier command.

### `./aether`

| Command | What it does |
| --- | --- |
| `./aether onboard` | First-time setup (safe to re-run) |
| `./aether up` / `./aether down` | Start / stop; data is kept |
| `./aether status` | Containers and API readiness |
| `./aether logs` | Follow API logs (JSON, no keys) |
| `./aether update` | `git pull` and restart |
| `./aether admin "Name"` | Create another admin key (printed once) |
| `AETHER_ADMIN_KEY=ae_... ./aether agent "my-agent"` | Register an agent; prints its key once |
| `./aether mcp` | Print the MCP connect command |
| `./aether demo` | Run the agent workflow demo |

The same with plain Docker: `docker compose up -d --build`, `docker compose down`
(`down -v` **deletes all data**), `docker compose exec ollama ollama pull all-minilm`,
`docker compose exec ollama ollama signin`, and
`docker compose exec api python /app/scripts/contributor.py create-admin "Name"`.
To change the chat model later, edit `AETHER_LLM_BASE_URL`, `AETHER_LLM_MODEL`,
and `AETHER_LLM_API_KEY` in `.env` and run `./aether up`.

### Use the API

Check it is up:

```sh
curl http://127.0.0.1:8000/health      # {"status":"ok"}
curl http://127.0.0.1:8000/ready       # {"status":"ready"} once Neo4j is reachable
```

Add knowledge (needs a key with `propose`). Extraction and community rebuilds
run in the background; poll the `GET` until it reports `complete`:

```sh
API=http://127.0.0.1:8000/api/v0
curl -X POST "$API/documents?filename=notes.txt" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: text/plain; charset=utf-8" --data-binary @notes.txt   # returns the document id
curl -X POST "$API/documents/$ID/extraction" -H "Authorization: Bearer $KEY"
curl "$API/documents/$ID/extraction"                                      # status
curl -X POST "$API/communities/rebuild" -H "Authorization: Bearer $KEY"   # needed for global questions
curl "$API/communities/rebuild"                                           # status
```

Ask (no key needed; `mode` is `local`, `global`, or `hybrid`):

```sh
curl -X POST "$API/query" -H "Content-Type: application/json" \
  -d '{"question": "Who discovered the neutron?", "mode": "hybrid"}'
```

Register an agent (admin key) and review its claims (human key with `review`):

```sh
curl -X POST "$API/contributors" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"type": "agent", "display_name": "my-agent"}'                        # prints the agent key once
curl "$API/claims?status=proposed"
curl -X POST "$API/claims/$ID/review" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"decision": "accept", "notes": "checked the source"}'                # or "reject"
```

Every route, field, and error is in [docs/openapi.json](docs/openapi.json), or
browse it at `http://127.0.0.1:8000/docs`.

## Use it from an agent (MCP)

`examples/mcp_server.py` exposes Aether to MCP clients such as Claude Code,
Cursor, or OpenClaw: `aether_query`, `aether_search_entities`,
`aether_neighborhood`, and `aether_propose_claim`.

With the Docker stack running, the server runs inside the API container, so
nothing else needs installing:

```sh
claude mcp add aether -- docker compose -f /path/to/Aether/docker-compose.yml \
  exec -T -e AETHER_API_KEY=ae_... api python /app/examples/mcp_server.py
```

Other MCP clients take the same command (`docker` plus those arguments). With
the Nix setup instead, run `python examples/mcp_server.py` with `AETHER_URL`
and `AETHER_API_KEY` set.

Querying works without a key; proposing claims needs an agent key from an
admin ([AGENTS.md](docs/AGENTS.md)). Proposed claims affect answers only after
a human accepts them.

## For AI agents

If you are an AI agent setting up or using Aether, follow these steps exactly.

1. **Check prerequisites:** `git --version` and `docker compose version` must succeed.
2. **Start:** humans usually run `./aether onboard` (interactive). Without a
   terminal for prompts, in the repository folder: if `.env` does not exist, run
   `echo "NEO4J_PASSWORD=$(openssl rand -hex 24)" > .env`; then
   `docker compose up -d --build`.
3. **Wait** until `curl -s http://127.0.0.1:8000/ready` returns `{"status":"ready"}`.
4. **Models:** run `docker compose exec ollama ollama pull all-minilm`. The chat
   model needs `docker compose exec ollama ollama signin`, which prints a URL a
   human must open; ask your user to do it.
5. **Keys:** your user creates the admin key (`create-admin`, see the command
   reference) and registers you as an `agent` contributor with it. Use only the
   agent key you are given; never ask for or hold the admin key, and never write
   keys to files or logs. Querying needs no key at all.
6. **Use it:** prefer the MCP tools above. Otherwise call the HTTP API with the
   Python or TypeScript client below.

Rules:

- An answer with an empty `citations` list means Aether has no support for it;
  say so instead of answering from your own knowledge.
- Propose a claim only with an `excerpt` copied verbatim from a passage you
  received (`text_unit_id` plus its exact text). Paraphrases are rejected (case and
  whitespace are ignored).
- You cannot verify claims; only a human reviewer can. Do not ask users for
  reviewer keys.
- On HTTP 429, wait for the `Retry-After` seconds; the bundled clients do this.

## Other languages

Any language can use the HTTP API ([OpenAPI contract](docs/openapi.json)).
Ready-made clients: Python ([examples/agent_client.py](examples/agent_client.py))
and TypeScript ([sdks/typescript](sdks/typescript)).

## Development (Nix)

The dev shell targets `x86_64-linux`; see [CONTRIBUTING.md](CONTRIBUTING.md).

```sh
nix develop
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev,mcp]"
python scripts/local_neo4j.py init && python scripts/local_neo4j.py start
ollama pull all-minilm && ollama signin
python scripts/demo.py
```

## Evaluation

Domain packs (`domains/`) bundle a corpus with golden questions.
`python scripts/eval.py --domain <pack>` runs one in a throwaway Neo4j instance
and scores keyword recall, citation precision, and abstention (answering
unanswerable questions with no citations). On the bundled packs:

| Pack | Questions | Recall | Precision | Abstention |
| --- | --- | --- | --- | --- |
| `radioactivity` | 12 | 0.95–1.0 | 1.0 | 1.0 |
| `curie-sample` | 11 | 1.0 | 1.0 | 1.0 |

Measured with `gpt-oss:120b-cloud` and `all-minilm`; recall varies between runs.

## Docs

- [Integrating an agent](docs/AGENTS.md) and a dependency-free
  [example client](examples/agent_client.py)
- [Self-hosting on a VPS](docs/DEPLOY.md)
- [Architecture](docs/ARCHITECTURE.md) and [roadmap](docs/ROADMAP.md)
- [Local database](docs/LOCAL_DATABASE.md)
- [Contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE)
