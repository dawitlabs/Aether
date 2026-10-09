"""Minimal client for any OpenAI-compatible endpoint (Ollama, Groq, Gemini, ...).

Use one instance per model: chat and embeddings may live on different servers.
With cache_dir set, chat_json answers repeated (model, system, user) inputs from
disk without calling the provider. Delete the directory to clear it.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """The provider failed or returned an unusable response."""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 120,
        retries: int = 3,
        cache_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._retries = retries
        self._cache_dir = cache_dir
        # In-process counters for /admin/stats; plain ints, approximate under threads.
        self.calls = 0
        self.cache_hits = 0

    def _cache_path(self, system: str, user: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = hashlib.sha256(json.dumps([self.model, system, user]).encode()).hexdigest()
        return self._cache_dir / f"{key}.json"

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        path = self._cache_path(system, user)
        if path is not None and path.exists():
            try:
                cached = json.loads(path.read_text())
                if isinstance(cached, dict):
                    self.cache_hits += 1
                    return cached
            except (OSError, json.JSONDecodeError):
                pass
        parsed = self._chat(system, user)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(parsed))
            os.replace(temp, path)
        return parsed

    def _chat(self, system: str, user: str) -> dict[str, Any]:
        body = self._post("/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        })
        try:
            parsed = json.loads(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise LLMError("Model did not return a JSON message") from error
        if not isinstance(parsed, dict):
            raise LLMError("Model returned JSON that is not an object")
        return parsed

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = self._post("/embeddings", {"model": self.model, "input": texts})
        try:
            rows = sorted(body["data"], key=lambda row: row["index"])
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as error:
            raise LLMError("Provider returned malformed embeddings") from error
        if len(vectors) != len(texts):
            raise LLMError(f"Expected {len(texts)} embeddings, got {len(vectors)}")
        return vectors

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode(), headers=headers
        )
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code not in RETRY_STATUSES or attempt == self._retries:
                    raise LLMError(f"Provider returned HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == self._retries:
                    raise LLMError("Provider unreachable") from error
            except json.JSONDecodeError as error:
                raise LLMError("Provider returned invalid JSON") from error
            # ponytail: fixed exponential backoff, ignores Retry-After; honor it if
            # free-tier rate limits make this too eager.
            time.sleep(2**attempt)
        raise AssertionError("unreachable: the loop returns or raises")
