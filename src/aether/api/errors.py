"""One error shape for every failure: {"error": <code>, "detail": <message>}."""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import DriverError, Neo4jError
from starlette.exceptions import HTTPException

from aether.extraction.llm import LLMError

log = logging.getLogger("aether.api")
CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
    415: "unsupported_media_type", 422: "invalid_request", 429: "rate_limited",
}


def error(status: int, code: str, detail: object, headers: dict[str, str] | None = None
          ) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error(exc.status_code, CODES.get(exc.status_code, "error"), exc.detail,
                     exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop the echoed input: it may contain secrets or large bodies.
        details = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
        return error(422, "invalid_request", jsonable_encoder(details))

    @app.exception_handler(DriverError)
    @app.exception_handler(Neo4jError)
    async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        log.warning("neo4j.request_failed error=%s", type(exc).__name__)
        return error(503, "database_unavailable", "Database unavailable")

    @app.exception_handler(LLMError)
    async def provider_unavailable(request: Request, exc: LLMError) -> JSONResponse:
        log.warning("llm.request_failed error=%s", exc)
        return error(502, "provider_unavailable", "Model provider unavailable")
