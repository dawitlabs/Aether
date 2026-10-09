"""Run the golden-question evaluation against the local stack.

Ingests eval/corpus (idempotent), extracts it, rebuilds all communities, asks
every question in eval/golden.jsonl, and appends scores to
.local/eval/<timestamp>.jsonl. Needs Neo4j and the configured LLM endpoints.
Cached LLM responses make repeat runs fast and identical.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase

from aether.api.app import Settings
from aether.communities.reports import rebuild_with_reports
from aether.evaluation import Golden, score, summarize
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
from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = Settings.from_env()
    chat = LLMClient(config.llm_base_url, config.llm_model,
                     api_key=config.llm_api_key, cache_dir=config.llm_cache_dir)
    embedder = LLMClient(config.embed_base_url, config.embed_model,
                         api_key=config.embed_api_key)
    goldens = [Golden.model_validate_json(line)
               for line in (ROOT / "eval/golden.jsonl").read_text().splitlines() if line.strip()]

    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_username, config.neo4j_password)
    ) as driver:
        db = config.neo4j_database
        ensure_schema(driver, db)
        documents = Neo4jDocumentStore(driver, db)
        extractor = Extractor(chat, embedder, Neo4jKnowledgeStore(driver, db), config.index_dir)
        filenames = {}
        for path in sorted((ROOT / "eval/corpus").glob("*.txt")):
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
        "kind": "summary", "at": datetime.now(timezone.utc).isoformat(),
        "llm_model": config.llm_model, "embed_model": config.embed_model,
        "extract_prompt": PROMPT_VERSION, "questions": len(scores), **summary,
    }
    out = ROOT / ".local/eval" / f"{run['at'].replace(':', '-')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(
        json.dumps(row, ensure_ascii=False) + "\n"
        for row in [run, *({"kind": "question", **s.model_dump()} for s in scores)]
    ))

    for s in scores:
        recall = "-" if s.keyword_recall is None else f"{s.keyword_recall:.2f}"
        precision = "-" if s.citation_precision is None else f"{s.citation_precision:.2f}"
        mark = "ok" if s.abstention_correct else "WRONG"
        print(f"{s.mode:<7} recall={recall:<5} precision={precision:<5} abstain={mark:<5} "
              f"{s.question}")
    print(f"\nsummary {summary}\nlogged to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
