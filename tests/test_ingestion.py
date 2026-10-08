import pytest
from fastapi.testclient import TestClient

from aether.api.app import create_app
from aether.ingestion import save_original, split_text
from test_api import UNREACHABLE

TEXT = "First paragraph here.\n\nSecond one is a bit longer.\nIt has two lines.\n\n   \n\nEnd"


def test_spans_are_exact_slices_within_the_limit():
    spans = split_text(TEXT, max_chars=30)
    assert all(end - start <= 30 for start, end in spans)
    assert all(TEXT[start:end].strip() for start, end in spans)
    assert "".join(TEXT[s:e] for s, e in spans).split() == TEXT.split()


def test_breaks_prefer_paragraphs_then_lines():
    spans = split_text(TEXT, max_chars=30)
    assert TEXT[spans[0][0]:spans[0][1]] == "First paragraph here.\n\n"
    assert TEXT[spans[1][0]:spans[1][1]] == "Second one is a bit longer.\n"


def test_unbroken_text_is_cut_at_the_limit():
    assert split_text("x" * 25, max_chars=10) == [(0, 10), (10, 20), (20, 25)]


def test_whitespace_only_text_has_no_spans():
    assert split_text(" \n\n \t") == []


def test_save_original_is_idempotent(tmp_path):
    save_original(tmp_path, "abc", b"one")
    save_original(tmp_path, "abc", b"one")
    assert [p.name for p in tmp_path.iterdir()] == ["abc.txt"]
    assert (tmp_path / "abc.txt").read_bytes() == b"one"


@pytest.mark.parametrize(
    "headers,body,status",
    [
        ({"content-type": "application/json"}, b"{}", 415),
        ({"content-type": "text/plain; charset=latin-1"}, b"x", 415),
        ({"content-type": "text/plain"}, b"x" * (1_048_576 + 1), 413),
        ({"content-type": "text/plain"}, b"hello", 503),
    ],
)
def test_upload_rejections_without_database(headers, body, status):
    with TestClient(create_app(UNREACHABLE)) as client:
        response = client.post("/documents", content=body, headers=headers)
    assert response.status_code == status
    assert "Traceback" not in response.text


def test_invalid_document_id_is_rejected():
    with TestClient(create_app(UNREACHABLE)) as client:
        assert client.get("/documents/not-a-uuid").status_code == 422
