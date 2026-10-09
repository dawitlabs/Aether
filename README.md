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

## Quickstart (local)

Needs [Nix](https://nixos.org/download) with flakes (the dev shell targets
`x86_64-linux`) and [Ollama](https://ollama.com).

```sh
git clone https://github.com/dawitlabs/Aether.git && cd Aether
nix develop
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"

python scripts/local_neo4j.py init     # writes .env with a random password
python scripts/local_neo4j.py start
ollama pull all-minilm && ollama signin  # embeddings + the gpt-oss:120b-cloud chat model

python scripts/demo.py --domain radioactivity
```

The demo builds a graph from the bundled `radioactivity` pack, then an agent
asks questions, proposes a claim, and sees it used once a human accepts it.

To run the API instead:

```sh
python scripts/contributor.py create-admin "Your Name"   # prints an admin key once
uvicorn --factory aether.api.app:create_app              # http://127.0.0.1:8000/docs
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
