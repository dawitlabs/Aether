from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aether.api.app import Settings, create_app

PLAIN = {"content-type": "text/plain; charset=utf-8"}


@pytest.fixture
def client(database, tmp_path, auth):
    driver, name = database
    uploaded = []
    settings = Settings.from_env().model_copy(update={"documents_dir": tmp_path})
    with TestClient(create_app(settings), headers=auth) as test_client:
        yield test_client, uploaded, tmp_path
    driver.execute_query(
        "MATCH (n) WHERE (n:Document AND n.id IN $ids) "
        "OR (n:TextUnit AND n.source_document_id IN $ids) DELETE n",
        parameters_={"ids": uploaded}, database_=name,
    )


def upload(client, body, **params):
    test_client, uploaded, _ = client
    response = test_client.post("/documents", content=body, headers=PLAIN, params=params)
    if response.status_code in (200, 201):
        uploaded.append(response.json()["id"])
    return response


def test_upload_lookup_and_list_trace_back_to_the_original(client):
    text = f"Nix — ሰላም {uuid4()}.\n\n" + ("word " * 600)
    body = text.encode("utf-8")
    response = upload(client, body, filename="notes.txt")
    assert response.status_code == 201
    document = response.json()
    assert document["filename"] == "notes.txt"
    assert document["size_bytes"] == len(body)
    assert document["content_hash"] == sha256(body).hexdigest()
    assert document["text_unit_count"] > 1

    test_client, _, originals = client
    assert test_client.get(f"/documents/{document['id']}").json() == document
    assert (originals / f"{document['content_hash']}.txt").read_bytes() == body

    units = test_client.get(f"/documents/{document['id']}/text-units").json()
    assert len(units) == document["text_unit_count"]
    for unit in units:
        assert text[unit["start_offset"]:unit["end_offset"]] == unit["text"]
        assert unit["token_count"] == len(unit["text"].split())
    assert units == sorted(units, key=lambda u: u["start_offset"])

    page = test_client.get(
        f"/documents/{document['id']}/text-units", params={"limit": 1, "offset": 1}
    ).json()
    assert page == units[1:2]


def test_reupload_returns_the_same_document(client):
    body = f"Same bytes {uuid4()}".encode()
    first = upload(client, body, filename="a.txt")
    second = upload(client, body, filename="b.txt")
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == first.json()


@pytest.mark.parametrize("body", [b"\xff\xfe not utf-8", b"   \n\n  "])
def test_unusable_text_is_rejected(client, body):
    response = upload(client, body)
    assert response.status_code == 422


def test_unknown_document_is_404(client):
    test_client, _, _ = client
    missing = uuid4()
    assert test_client.get(f"/documents/{missing}").status_code == 404
    assert test_client.get(f"/documents/{missing}/text-units").status_code == 404


@pytest.mark.parametrize("headers,body,status", [
    ({"content-type": "application/json"}, b"{}", 415),
    ({"content-type": "text/plain; charset=latin-1"}, b"x", 415),
    ({"content-type": "text/plain"}, b"x" * (1_048_576 + 1), 413),
])
def test_upload_rejects_wrong_type_or_size(client, headers, body, status):
    test_client, _, _ = client
    assert test_client.post("/documents", content=body, headers=headers).status_code == status
