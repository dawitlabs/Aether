import pytest
from fastapi import HTTPException

from aether.api.limits import RateLimiter


def test_sliding_window_blocks_then_recovers():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    limiter.check("a", limit=2)
    now[0] = 30
    limiter.check("a", limit=2)
    with pytest.raises(HTTPException) as blocked:
        limiter.check("a", limit=2)
    assert blocked.value.status_code == 429
    assert blocked.value.headers["Retry-After"] == "30"
    limiter.check("b", limit=2)
    now[0] = 60
    limiter.check("a", limit=2)


def test_idle_keys_are_forgotten():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    for ip in range(100):
        limiter.check(f"query:{ip}", limit=1)
    now[0] = 61
    limiter.check("query:new", limit=1)
    assert list(limiter._hits) == ["query:new"]
