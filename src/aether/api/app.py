"""HTTP API. Run from the project root: uvicorn --factory aether.api.app:create_app"""

import logging
import os
from collections.abc import AsyncIterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ConfigDict, Field

from aether.api.graph import router as graph_router
from aether.core.models import Document, TextUnit
from aether.extraction.jobs import extract_document
from aether.extraction.llm import LLMClient, LLMError
from aether.extraction.pipeline import Extractor
from aether.ingestion import UploadError, ingest
from aether.storage.documents import Neo4jDocumentStore
from aether.storage.knowledge import Neo4jKnowledgeStore
from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore

log = logging.getLogger("aether.api")
MAX_UPLOAD_BYTES = 1_048_576
UNAVAILABLE = {"description": "Database unavailable"}


class ExtractionStatus(BaseModel):
    status: Literal["not_started", "running", "failed", "complete"]
    extracted: int
    total: int


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    neo4j_uri: str = Field(min_length=1)
    neo4j_username: str = Field(min_length=1)
    neo4j_password: str = Field(min_length=1, repr=False)
    neo4j_database: str = Field(min_length=1)
    documents_dir: Path = Path(".local/documents")
    index_dir: Path = Path(".local/lancedb")
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "gpt-oss:120b-cloud"
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_cache_dir: Path | None = Path(".local/llm-cache")
    embed_base_url: str = "http://127.0.0.1:11434/v1"
    embed_model: str = "all-minilm"
    embed_api_key: str | None = Field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        """Process environment overrides `.env` in the working directory."""
        values = {**dotenv_values(Path.cwd() / ".env"), **os.environ}
        return cls.model_validate({
            field: values[field.upper()]
            for field in cls.model_fields
            if values.get(field.upper()) is not None
        })


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Creating a driver does not connect, so the API starts while Neo4j is down.
        driver = GraphDatabase.driver(
            config.neo4j_uri,
            auth=(config.neo4j_username, config.neo4j_password),
            connection_timeout=2,
            # ponytail: fixed 3s retry budget (driver default is 30s per query);
            # make it a setting if a deployment needs longer.
            max_transaction_retry_time=3,
        )
        app.state.driver = driver
        app.state.schema_ready = apply_schema(driver)
        app.state.extractor = Extractor(
            LLMClient(
                config.llm_base_url, config.llm_model,
                api_key=config.llm_api_key, cache_dir=config.llm_cache_dir,
            ),
            LLMClient(config.embed_base_url, config.embed_model, api_key=config.embed_api_key),
            Neo4jKnowledgeStore(driver, config.neo4j_database),
            config.index_dir,
        )
        # One worker: units are extracted strictly one at a time (see pipeline.py).
        app.state.worker = ThreadPoolExecutor(max_workers=1)
        app.state.jobs = {}
        try:
            yield
        finally:
            # An interrupted unit is never marked done, so a re-run redoes it.
            app.state.worker.shutdown(wait=False, cancel_futures=True)
            driver.close()

    def apply_schema(driver: Driver) -> bool:
        try:
            ensure_schema(driver, config.neo4j_database)
        except (DriverError, Neo4jError) as error:
            log.warning("neo4j.schema_pending error=%s", type(error).__name__)
            return False
        return True

    app = FastAPI(title="Aether", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.include_router(graph_router)

    @app.exception_handler(DriverError)
    @app.exception_handler(Neo4jError)
    async def database_unavailable(request: Request, error: Exception) -> JSONResponse:
        log.warning("neo4j.request_failed error=%s", type(error).__name__)
        return JSONResponse({"detail": "Database unavailable"}, status_code=503)

    @app.exception_handler(LLMError)
    async def provider_unavailable(request: Request, error: LLMError) -> JSONResponse:
        log.warning("llm.request_failed error=%s", error)
        return JSONResponse({"detail": "Model provider unavailable"}, status_code=502)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Process liveness; never touches the database."""
        return {"status": "ok"}

    @app.get("/ready", responses={503: UNAVAILABLE})
    def ready(request: Request) -> JSONResponse:
        """Database readiness: schema applied and one authenticated query succeeds."""
        state = request.app.state
        if not state.schema_ready:
            state.schema_ready = apply_schema(state.driver)
        if not state.schema_ready:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        try:
            with request.app.state.driver.session(database=config.neo4j_database) as session:
                session.run("RETURN 1").consume()
        except (DriverError, Neo4jError) as error:
            log.warning("neo4j.unavailable error=%s", type(error).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.post(
        "/documents",
        status_code=201,
        responses={200: {"description": "Same bytes already uploaded"}, 503: UNAVAILABLE},
    )
    async def upload_document(
        request: Request,
        response: Response,
        filename: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    ) -> Document:
        """Upload a UTF-8 text/plain body, at most 1 MiB. Re-uploads return the original."""
        media_type, _, parameters = request.headers.get("content-type", "").partition(";")
        if media_type.strip().lower() != "text/plain" or parameters.strip().lower() not in (
            "", "charset=utf-8"
        ):
            raise HTTPException(415, "Send Content-Type: text/plain; charset=utf-8")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Document exceeds 1 MiB")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Document exceeds 1 MiB")
        store = Neo4jDocumentStore(request.app.state.driver, config.neo4j_database)
        try:
            document, created = await run_in_threadpool(
                ingest, bytes(body), filename, store, config.documents_dir
            )
        except UploadError as error:
            raise HTTPException(422, str(error)) from None
        response.status_code = 201 if created else 200
        return document

    @app.get("/documents/{document_id}", responses={404: {}, 503: UNAVAILABLE})
    def get_document(request: Request, document_id: UUID) -> Document:
        store = Neo4jDocumentStore(request.app.state.driver, config.neo4j_database)
        document = store.get(document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        return document

    @app.get("/documents/{document_id}/text-units", responses={404: {}, 503: UNAVAILABLE})
    def list_text_units(
        request: Request,
        document_id: UUID,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[TextUnit]:
        driver = request.app.state.driver
        if Neo4jDocumentStore(driver, config.neo4j_database).get(document_id) is None:
            raise HTTPException(404, "Document not found")
        units = Neo4jTextUnitStore(driver, config.neo4j_database)
        return units.list_by_document(document_id, limit=limit, offset=offset)

    def extraction_status(request: Request, document_id: UUID) -> ExtractionStatus:
        state = request.app.state
        store = Neo4jKnowledgeStore(state.driver, config.neo4j_database)
        extracted, total = store.extraction_progress(document_id, state.extractor.marker)
        job: Future[None] | None = state.jobs.get(document_id)
        if job is not None and not job.done():
            status = "running"
        elif extracted == total:
            status = "complete"
        elif job is not None and not job.cancelled() and job.exception() is not None:
            status = "failed"
        else:
            status = "not_started"
        return ExtractionStatus(status=status, extracted=extracted, total=total)

    def require_document(request: Request, document_id: UUID) -> None:
        store = Neo4jDocumentStore(request.app.state.driver, config.neo4j_database)
        if store.get(document_id) is None:
            raise HTTPException(404, "Document not found")

    @app.post(
        "/documents/{document_id}/extraction",
        status_code=202,
        responses={404: {}, 503: UNAVAILABLE},
    )
    def start_extraction(request: Request, document_id: UUID) -> ExtractionStatus:
        """Queue LLM extraction. Idempotent: finished units are skipped."""
        require_document(request, document_id)
        state = request.app.state
        job = state.jobs.get(document_id)
        if job is None or job.done():
            units = Neo4jTextUnitStore(state.driver, config.neo4j_database)
            job = state.worker.submit(extract_document, state.extractor, units, document_id)
            job.add_done_callback(log_failure)
            state.jobs[document_id] = job
        return extraction_status(request, document_id)

    @app.get("/documents/{document_id}/extraction", responses={404: {}, 503: UNAVAILABLE})
    def get_extraction(request: Request, document_id: UUID) -> ExtractionStatus:
        require_document(request, document_id)
        return extraction_status(request, document_id)

    return app


def log_failure(job: Future[None]) -> None:
    error = None if job.cancelled() else job.exception()
    if error is not None:
        log.warning("extraction.job_failed error=%s", type(error).__name__)
