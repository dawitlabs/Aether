"""Run a domain pack's golden-question evaluation against the local stack.

    python scripts/eval.py [--domain curie-sample]

Starts a throwaway Neo4j instance on a free loopback port with its own
documents and vector index, loads only this pack, extracts it, builds its
communities, asks every golden question, then stops and deletes the instance.
The dev graph is never read or written. Scores go to
.local/eval/<domain>-<timestamp>.jsonl. Needs the Nix shell (for the Neo4j
binaries) and the configured LLM endpoints. Cached extraction responses make
repeat runs fast, but answers can vary between runs: every instance assigns
fresh IDs, so query prompts miss the cache.
"""

import argparse
import json
import secrets
import socket
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import local_neo4j
from neo4j import GraphDatabase

from aether.api.app import Settings
from aether.communities.reports import rebuild_with_reports
from aether.domains import Pack, available, load_pack
from aether.evaluation import score, summarize
from aether.extraction.extract import PROMPT_VERSION
from aether.extraction.jobs import extract_document
from aether.extraction.llm import LLMClient
from aether.extraction.pipeline import Extractor
from aether.ingestion import ingest
from aether.query import answer_question
from aether.storage.claims import Neo4jClaimStore
from aether.storage.communities import Neo4jCommunityStore
from aether.storage.documents import Neo4jDocumentStore
from aether.storage.graph import Neo4jGraphReader
from aether.storage.knowledge import Neo4jKnowledgeStore
from aether.storage.merges import Neo4jMergeStore
from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def throwaway_settings(base: Settings, domain: str) -> Iterator[Settings]:
    """Yields `base` pointed at a fresh Neo4j instance and empty stores; deletes them after."""
    (ROOT / ".local/eval").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{domain}-", dir=ROOT / ".local/eval") as tmp:
        home = Path(tmp) / "neo4j"
        # ponytail: probe-then-bind race on the port; a clash just fails the start.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        local_neo4j.prepare_home(home, port, None)
        password = secrets.token_urlsafe(32)
        local_neo4j.command("neo4j-admin", "dbms", "set-initial-password", password,
                            private=True, home=home)
        config = base.model_copy(update={
            "neo4j_uri": f"bolt://127.0.0.1:{port}", "neo4j_username": "neo4j",
            "neo4j_password": password, "neo4j_database": "neo4j",
            "documents_dir": Path(tmp) / "documents", "index_dir": Path(tmp) / "lancedb",
        })
        local_neo4j.command("neo4j", "start", private=True, home=home)
        try:
            local_neo4j.check(wait=True, config={
                "NEO4J_URI": config.neo4j_uri, "NEO4J_USERNAME": "neo4j",
                "NEO4J_PASSWORD": password, "NEO4J_DATABASE": "neo4j",
            })
            yield config
        finally:
            local_neo4j.command("neo4j", "stop", private=True, home=home)


def main(domain: str) -> None:
    pack = load_pack(domain)
    with throwaway_settings(Settings.from_env(), domain) as config:
        run_pack(pack, domain, config)


def run_pack(pack: Pack, domain: str, config: Settings) -> None:
    chat = LLMClient(config.llm_base_url, config.llm_model,
                     api_key=config.llm_api_key, cache_dir=config.llm_cache_dir)
    embedder = LLMClient(config.embed_base_url, config.embed_model,
                         api_key=config.embed_api_key)
    goldens = pack.goldens

    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_username, config.neo4j_password),
        # The graph starts empty, so queries name labels and properties that do
        # not exist yet; the server's warnings about them are expected noise.
        notifications_min_severity="OFF",
    ) as driver:
        db = config.neo4j_database
        ensure_schema(driver, db)
        documents = Neo4jDocumentStore(driver, db)
        extractor = Extractor(chat, embedder, Neo4jKnowledgeStore(driver, db), config.index_dir,
                              merges=Neo4jMergeStore(driver, db))
        filenames = {}
        for path in pack.corpus():
            document, _ = ingest(path.read_bytes(), path.name, documents, config.documents_dir)
            filenames[document.id] = path.name
            extract_document(extractor, Neo4jTextUnitStore(driver, db), document.id)
        communities = Neo4jCommunityStore(driver, db)
        rebuild_with_reports(communities, chat, embedder, config.index_dir)

        graph = Neo4jGraphReader(driver, db)
        scores = []
        for golden in goldens:
            answer = answer_question(
                golden.question, golden.mode, chat=chat, embedder=embedder,
                graph=graph, communities=communities,
                claims=Neo4jClaimStore(driver, db), index_path=config.index_dir,
            )
            scores.append(score(golden, answer, filenames))

    summary = summarize(scores)
    run = {
        "kind": "summary", "domain": domain, "at": datetime.now(timezone.utc).isoformat(),
        "llm_model": config.llm_model, "embed_model": config.embed_model,
        "extract_prompt": PROMPT_VERSION, "questions": len(scores), **summary,
    }
    out = ROOT / ".local/eval" / f"{domain}-{run['at'].replace(':', '-')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(
        json.dumps(row, ensure_ascii=False) + "\n"
        for row in [run, *({"kind": "question", **s.model_dump()} for s in scores)]
    ))

    for s in scores:
        recall = "-" if s.keyword_recall is None else f"{s.keyword_recall:.2f}"
        precision = "-" if s.citation_precision is None else f"{s.citation_precision:.2f}"
        mark = "ok" if s.abstention_correct else "WRONG"
        print(f"{s.mode:<7} recall={recall:<5} precision={precision:<5} "
              f"foreign={s.foreign_citations}/{s.citations} abstain={mark:<5} {s.question}")
    print(f"\nsummary {summary}\nlogged to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="curie-sample", choices=available())
    main(parser.parse_args().domain)
