"""HTTP API. Run from the project root: uvicorn --factory aether.api.app:create_app"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ConfigDict, Field

from aether.core.models import Document, TextUnit
from aether.ingestion import UploadError, ingest
from aether.storage.documents import Neo4jDocumentStore
from aether.storage.schema import ensure_schema
from aether.storage.text_units import Neo4jTextUnitStore

log = logging.getLogger("aether.api")
MAX_UPLOAD_BYTES = 1_048_576
UNAVAILABLE = {"description": "Database unavailable"}


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    neo4j_uri: str = Field(min_length=1)
    neo4j_username: str = Field(min_length=1)
    neo4j_password: str = Field(min_length=1, repr=False)
    neo4j_database: str = Field(min_length=1)
    documents_dir: Path = Path(".local/documents")

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
        try:
            yield
        finally:
            driver.close()

    def apply_schema(driver: Driver) -> bool:
        try:
            ensure_schema(driver, config.neo4j_database)
        except (DriverError, Neo4jError) as error:
            log.warning("neo4j.schema_pending error=%s", type(error).__name__)
            return False
        return True

    app = FastAPI(title="Aether", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DriverError)
    @app.exception_handler(Neo4jError)
    async def database_unavailable(request: Request, error: Exception) -> JSONResponse:
        log.warning("neo4j.request_failed error=%s", type(error).__name__)
        return JSONResponse({"detail": "Database unavailable"}, status_code=503)

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

    return app
