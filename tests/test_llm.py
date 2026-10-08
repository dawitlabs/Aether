import io
import json
import urllib.error

import pytest

from aether.extraction import llm
from aether.extraction.llm import LLMClient, LLMError


class Queue(list):
    requests: list


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "error", {}, None)


@pytest.fixture
def respond(monkeypatch):
    """Queue responses (dicts or exceptions) for successive requests."""
    queue = Queue()
    queue.requests = requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(json.dumps(item).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    return queue


def chat_reply(content):
    return {"choices": [{"message": {"content": content}}]}


def test_chat_json_parses_object_and_sends_json_mode(respond):
    respond.append(chat_reply('{"entities": []}'))
    client = LLMClient("http://host/v1/", "m", api_key="secret")
    assert client.chat_json("sys", "user") == {"entities": []}
    request = respond.requests[0]
    assert request.full_url == "http://host/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret"
    payload = json.loads(request.data)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "m"


@pytest.mark.parametrize("content", ["not json", "[1, 2]", None])
def test_chat_json_rejects_non_object_content(respond, content):
    respond.append(chat_reply(content))
    with pytest.raises(LLMError):
        LLMClient("http://host", "m").chat_json("s", "u")


def test_retries_rate_limit_then_succeeds(respond):
    respond.extend([http_error(429), http_error(503), chat_reply("{}")])
    assert LLMClient("http://host", "m").chat_json("s", "u") == {}
    assert len(respond.requests) == 3


def test_gives_up_after_retries(respond):
    respond.extend([http_error(429)] * 3)
    with pytest.raises(LLMError, match="HTTP 429"):
        LLMClient("http://host", "m", retries=2).chat_json("s", "u")


def test_does_not_retry_client_errors(respond):
    respond.append(http_error(401))
    with pytest.raises(LLMError, match="HTTP 401"):
        LLMClient("http://host", "m").chat_json("s", "u")
    assert len(respond.requests) == 1


def test_unreachable_provider_raises_after_retries(respond):
    respond.extend([urllib.error.URLError("refused")] * 2)
    with pytest.raises(LLMError, match="unreachable"):
        LLMClient("http://host", "m", retries=1).chat_json("s", "u")


def test_embed_orders_by_index_and_checks_count(respond):
    respond.append({"data": [
        {"index": 1, "embedding": [0, 1]},
        {"index": 0, "embedding": [1, 0]},
    ]})
    client = LLMClient("http://host", "embed")
    assert client.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    respond.append({"data": [{"index": 0, "embedding": [1, 0]}]})
    with pytest.raises(LLMError, match="Expected 2"):
        client.embed(["a", "b"])


def test_cache_answers_repeats_without_provider_calls(respond, tmp_path):
    respond.append(chat_reply('{"n": 1}'))
    client = LLMClient("http://host", "m", cache_dir=tmp_path)
    assert client.chat_json("s", "u") == {"n": 1}
    assert LLMClient("http://host", "m", cache_dir=tmp_path).chat_json("s", "u") == {"n": 1}
    assert len(respond.requests) == 1

    respond.append(chat_reply('{"n": 2}'))
    assert LLMClient("http://host", "other", cache_dir=tmp_path).chat_json("s", "u") == {"n": 2}


def test_corrupt_cache_entry_is_refetched(respond, tmp_path):
    client = LLMClient("http://host", "m", cache_dir=tmp_path)
    respond.append(chat_reply('{"n": 1}'))
    client.chat_json("s", "u")
    next(tmp_path.glob("*.json")).write_text("{trunc")
    respond.append(chat_reply('{"n": 2}'))
    assert client.chat_json("s", "u") == {"n": 2}
    assert len(respond.requests) == 2


def test_failed_calls_are_not_cached(respond, tmp_path):
    respond.append(chat_reply("not json"))
    with pytest.raises(LLMError):
        LLMClient("http://host", "m", cache_dir=tmp_path).chat_json("s", "u")
    assert list(tmp_path.iterdir()) == []
