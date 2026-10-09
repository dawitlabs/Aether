"""In-process sliding-window rate limits.

ponytail: per-process memory; with several workers each enforces its own
window. Move the counters to a shared store if the API is ever scaled out.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._swept = clock()

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        """Count one hit for key; raise 429 with Retry-After once over the limit."""
        now = self._clock()
        with self._lock:
            # Once per window, forget keys with no hits inside it; otherwise every
            # client IP ever seen stays in memory.
            if now - self._swept >= window:
                self._hits = {k: v for k, v in self._hits.items() if v[-1] > now - window}
                self._swept = now
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                retry = math.ceil(hits[0] + window - now)
                raise HTTPException(429, "Rate limit exceeded",
                                    headers={"Retry-After": str(max(retry, 1))})
            hits.append(now)
