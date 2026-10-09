import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openapi_script", ROOT / "scripts/openapi.py")
openapi_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openapi_script)


def test_api_contract_matches_committed_snapshot():
    assert openapi_script.contract() == openapi_script.CONTRACT.read_text(), (
        "API contract changed. If intended, run: python scripts/openapi.py"
    )


def test_every_api_route_is_versioned():
    paths = openapi_script.create_app(openapi_script.OFFLINE).openapi()["paths"]
    assert set(paths) - {"/health", "/ready"} == {p for p in paths if p.startswith("/api/v0/")}


def test_errors_share_one_shape_and_version_is_reported():
    app = openapi_script.create_app(openapi_script.OFFLINE)
    with TestClient(app, base_url="http://testserver/api/v0") as client:
        missing = client.get("/nope")
        invalid = client.get("/documents/not-a-uuid")
        version = client.get("/version").json()
    assert missing.json() == {"error": "not_found", "detail": "Not Found"}
    assert invalid.json()["error"] == "invalid_request"
    assert "input" not in invalid.json()["detail"][0]
    assert version["api"] == "v0" and version["version"]
