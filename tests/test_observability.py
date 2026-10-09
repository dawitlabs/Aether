import json
import logging

from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app
from aether.api.observability import JsonFormatter, request_id

OFFLINE = Settings(neo4j_uri="bolt://127.0.0.1:1", neo4j_username="-",
                   neo4j_password="-", neo4j_database="-")


def test_request_ids_are_echoed_generated_or_replaced():
    with TestClient(create_app(OFFLINE)) as client:
        given = client.get("/health", headers={"X-Request-ID": "abc-123"})
        generated = client.get("/health")
        hostile = client.get("/health", headers={"X-Request-ID": "x\" injected"})
    assert given.headers["x-request-id"] == "abc-123"
    assert len(generated.headers["x-request-id"]) == 32
    assert hostile.headers["x-request-id"] != "x\" injected"


def test_request_log_has_route_and_timing_but_no_secrets_or_query(caplog):
    caplog.set_level(logging.INFO, logger="aether.http")
    with TestClient(create_app(OFFLINE), base_url="http://testserver/api/v0") as client:
        client.get("/version?name=private-term", headers={"Authorization": "Bearer ae_secret"})
    record = next(r for r in caplog.records if r.getMessage() == "http.request")
    assert record.fields["route"] == "/api/v0/version"
    assert record.fields["status"] == 200
    assert record.fields["duration_ms"] >= 0
    line = JsonFormatter().format(record)
    assert "ae_secret" not in line and "private-term" not in line


def test_formatter_emits_one_json_object_with_request_id():
    token = request_id.set("rid-1")
    try:
        record = logging.LogRecord("aether.x", logging.WARNING, __file__, 1, "evt %s", ("a",), None)
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)
    assert entry["message"] == "evt a" and entry["level"] == "warning"
    assert entry["request_id"] == "rid-1"
