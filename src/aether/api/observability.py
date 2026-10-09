"""JSON logs with request IDs, request timing, and in-process request counters.

Logs carry the method, route template, status, and duration. Query strings and
headers are never logged, so neither keys nor search terms reach the logs.
ponytail: counters are per process; export to a metrics backend (OpenTelemetry)
when the API runs as more than one worker.
"""

import json
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response

log = logging.getLogger("aether.http")
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
VALID_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
API_PREFIX = "/api/v0"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging() -> None:
    logger = logging.getLogger("aether")
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def install(app: FastAPI) -> None:
    app.state.requests = Counter()

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id.reset(token)
        route = getattr(request.scope.get("route"), "path", request.url.path)
        # Included routers report templates without their prefix.
        if request.url.path.startswith(API_PREFIX + "/") and not route.startswith(API_PREFIX):
            route = API_PREFIX + route
        app.state.requests[f"{response.status_code // 100}xx"] += 1
        response.headers["X-Request-ID"] = rid
        log.info("http.request", extra={"fields": {
            "request_id": rid, "method": request.method, "route": route,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }})
        return response
