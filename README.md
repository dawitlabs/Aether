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

## Quickstart (Docker)

Works on Linux, macOS, and Windows with Docker Compose.

> The Docker setup is new: Neo4j, the API, admin keys and uploads are tested;
> the Ollama container steps are not yet. Please
> [open an issue](https://github.com/dawitlabs/Aether/issues) if one fails.

```sh
git clone https://github.com/dawitlabs/Aether.git && cd Aether
echo "NEO4J_PASSWORD=$(openssl rand -hex 24)" > .env
docker compose up -d --build

docker compose exec ollama ollama pull all-minilm   # embeddings
docker compose exec ollama ollama signin            # chat model gpt-oss:120b-cloud
docker compose exec api python /app/scripts/contributor.py create-admin "Your Name"
```

The last command prints an admin key once. The API is at
`http://127.0.0.1:8000` (docs at `/docs`). Try the whole agent workflow on the
bundled `radioactivity` pack:

```sh
docker compose exec api python /app/scripts/demo.py
```

To use another OpenAI-compatible provider, edit the `LLM_*`/`EMBED_*` values
in `docker-compose.yml` and put keys in `.env`. For a public server, see
[Self-hosting on a VPS](docs/DEPLOY.md).

## Use it from an agent (MCP)

`examples/mcp_server.py` exposes Aether to MCP clients such as Claude Code,
Cursor, or OpenClaw: `aether_query`, `aether_search_entities`,
`aether_neighborhood`, and `aether_propose_claim`.

```sh
pip install "mcp==2.3.0"
claude mcp add aether -e AETHER_URL=http://127.0.0.1:8000 -e AETHER_API_KEY=ae_... \
  -- python /path/to/Aether/examples/mcp_server.py
```

Querying works without a key; proposing claims needs an agent key from an
admin ([AGENTS.md](docs/AGENTS.md)). Proposed claims affect answers only after
a human accepts them.

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
