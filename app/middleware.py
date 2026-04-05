"""
Lightweight in-memory rate limiter (sliding window, per-IP).

This is intentionally simple: it works for single-process deployments.
For multi-process or multi-node setups, replace the backing store with Redis.
"""

import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import Request, HTTPException, status

from .config import settings


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        # ip -> deque of timestamps (epoch seconds)
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)

    def check(self, ip: str) -> None:
        """Raise HTTP 429 if *ip* has exceeded the request quota."""
        if not settings.rate_limit_enabled:
            return

        now = time.monotonic()
        cutoff = now - self._window
        bucket = self._buckets[ip]

        # Drop timestamps outside the sliding window.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self._max:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {self._max} requests "
                    f"per {self._window}s."
                ),
                headers={"Retry-After": str(self._window)},
            )

        bucket.append(now)


# Module-level singleton; wired into FastAPI as a dependency.
_limiter = RateLimiter(
    max_requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
)


def rate_limit(request: Request) -> None:
    """FastAPI dependency — call this in every endpoint that needs limiting."""
    client_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or (request.client.host if request.client else "unknown")
    )
    _limiter.check(client_ip)
