"""Minimal Aether API client for agents. Standard library only.

    from agent_client import AetherClient
    aether = AetherClient("http://127.0.0.1:8000", api_key="ae_...")
    answer = aether.query("Who founded the Radium Institute?", mode="hybrid")
    claim = aether.propose_claim(
        "The Radium Institute was founded in 1909.",
        evidence=[(text_unit_id, "founded in 1909")],
    )

Every call raises AetherError on a non-2xx response. A 429 is retried after
its Retry-After delay, at most `retries` times.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

# (method, url, headers, body) -> (status, headers, body)
Send = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]


class AetherError(Exception):
    def __init__(self, status: int, error: str, detail: Any) -> None:
        super().__init__(f"{status} {error}: {detail}")
        self.status, self.error, self.detail = status, error, detail


def urllib_send(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class AetherClient:
    def __init__(self, base_url: str, api_key: str | None = None, *,
                 send: Send = urllib_send, retries: int = 3) -> None:
        self.base = base_url.rstrip("/") + "/api/v0"
        self.api_key = api_key
        self._send = send
        self._retries = retries

    def request(self, method: str, path: str, body: Any = None, *,
                content_type: str = "application/json") -> Any:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            headers["Content-Type"] = content_type
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
        for attempt in range(self._retries + 1):
            status, response_headers, raw = self._send(method, self.base + path, headers, data)
            retry_after = {k.lower(): v for k, v in response_headers.items()}.get("retry-after")
            if status == 429 and attempt < self._retries and retry_after:
                time.sleep(int(retry_after))
                continue
            break
        payload = json.loads(raw) if raw else None
        if not 200 <= status < 300:
            payload = payload if isinstance(payload, dict) else {}
            raise AetherError(status, payload.get("error", "error"), payload.get("detail"))
        return payload

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/contributors/me")

    def search_entities(self, name: str) -> list[dict[str, Any]]:
        return self.request("GET", f"/entities?name={urllib.parse.quote(name)}")

    def neighborhood(self, entity_id: str) -> dict[str, Any]:
        return self.request("GET", f"/entities/{entity_id}/neighborhood")

    def upload(self, text: str, filename: str | None = None) -> dict[str, Any]:
        path = f"/documents?filename={urllib.parse.quote(filename)}" if filename else "/documents"
        return self.request("POST", path, text.encode(),
                            content_type="text/plain; charset=utf-8")

    def query(self, question: str, mode: str = "local") -> dict[str, Any]:
        return self.request("POST", "/query", {"question": question, "mode": mode})

    def propose_claim(self, statement: str, evidence: list[tuple[str, str]], *,
                      subject_id: str | None = None, confidence: float = 0.8) -> dict[str, Any]:
        """evidence: (text_unit_id, verbatim excerpt) pairs."""
        return self.request("POST", "/claims", {
            "statement": statement, "subject_id": subject_id, "confidence": confidence,
            "evidence": [{"text_unit_id": str(u), "excerpt": e} for u, e in evidence],
        })

    def claim(self, claim_id: str) -> dict[str, Any]:
        return self.request("GET", f"/claims/{claim_id}")

    def dispute(self, claim_id: str, reason: str,
                counter_evidence: list[tuple[str, str]]) -> dict[str, Any]:
        return self.request("POST", f"/claims/{claim_id}/dispute", {
            "reason": reason,
            "counter_evidence": [{"text_unit_id": str(u), "excerpt": e}
                                 for u, e in counter_evidence],
        })
