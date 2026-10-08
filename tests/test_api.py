import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import DriverError
from pydantic import ValidationError

from aether.api.app import Settings, create_app

SECRET = "not-a-real-password"
# Nothing listens on port 1, so connections are refused immediately.
UNREACHABLE = Settings(
    neo4j_uri="bolt://127.0.0.1:1",
    neo4j_username="neo4j",
    neo4j_password=SECRET,
    neo4j_database="neo4j",
)


def test_health_does_not_need_the_database():
    with TestClient(create_app(UNREACHABLE)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_unavailable_without_internal_details(caplog):
    with TestClient(create_app(UNREACHABLE)) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "neo4j.schema_pending" in caplog.text
    assert SECRET not in caplog.text


def test_openapi_documents_both_endpoints():
    with TestClient(create_app(UNREACHABLE)) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/ready"} <= paths.keys()


def test_shutdown_closes_the_driver():
    app = create_app(UNREACHABLE)
    with TestClient(app):
        driver = app.state.driver
    with pytest.raises(DriverError, match="closed"):
        driver.session().run("RETURN 1")


def test_settings_read_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in UNREACHABLE.model_dump(mode="json").items():
        if value is not None:
            monkeypatch.setenv(key.upper(), value)
    assert Settings.from_env() == UNREACHABLE


def test_invalid_settings_never_echo_the_password(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEO4J_PASSWORD", SECRET)
    for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_DATABASE"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError) as error:
        Settings.from_env()
    assert SECRET not in str(error.value)
    assert SECRET not in repr(UNREACHABLE)
