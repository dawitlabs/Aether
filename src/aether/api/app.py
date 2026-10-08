"""HTTP API. Run from the project root: uvicorn --factory aether.api.app:create_app"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("aether.api")


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    neo4j_uri: str = Field(min_length=1)
    neo4j_username: str = Field(min_length=1)
    neo4j_password: str = Field(min_length=1, repr=False)
    neo4j_database: str = Field(min_length=1)

    @classmethod
    def from_env(cls) -> "Settings":
        """Process environment overrides `.env` in the working directory."""
        values = {**dotenv_values(Path.cwd() / ".env"), **os.environ}
        return cls.model_validate(
            {field: values.get(field.upper()) for field in cls.model_fields}
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Creating a driver does not connect, so the API starts while Neo4j is down.
        driver = GraphDatabase.driver(
            config.neo4j_uri,
            auth=(config.neo4j_username, config.neo4j_password),
            connection_timeout=2,
        )
        app.state.driver = driver
        try:
            yield
        finally:
            driver.close()

    app = FastAPI(title="Aether", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Process liveness; never touches the database."""
        return {"status": "ok"}

    @app.get("/ready", responses={503: {"description": "Database unavailable"}})
    def ready(request: Request) -> JSONResponse:
        """Database readiness: one authenticated query, without retries."""
        try:
            with request.app.state.driver.session(database=config.neo4j_database) as session:
                session.run("RETURN 1").consume()
        except (DriverError, Neo4jError) as error:
            log.warning("neo4j.unavailable error=%s", type(error).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return app
