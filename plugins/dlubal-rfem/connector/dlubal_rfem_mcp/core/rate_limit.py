"""Token bucket rate limiter per API key."""

import logging
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("dlubal_rfem_mcp.rate_limit")

# Authentication runs before this middleware. Keep its metadata probe available
# when tool traffic exhausts the quota, so the watchdog does not restart a healthy
# server (and interrupt an active calculation) because of a rate-limit response.
_UNMETERED_PATHS = frozenset({"/health", "/favicon.ico", "/auth-check", "/control"})


class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity, self.tokens + (now - self.last_refill) * self.rate
            )
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 30):
        super().__init__(app)
        self.rpm = requests_per_minute
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, key: str) -> TokenBucket:
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(
                    rate=self.rpm / 60.0, capacity=self.rpm
                )
            return self._buckets[key]

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _UNMETERED_PATHS:
            return await call_next(request)

        api_key = getattr(request.state, "api_key", "anonymous")
        if not self._bucket(api_key).consume():
            logger.debug("MCP rate limit exceeded")
            return JSONResponse(
                status_code=429,
                content={
                    "error": f"Rate limit exceeded. Maximum {self.rpm} requests per minute."
                },
                headers={"Retry-After": str(int(60 / self.rpm))},
            )
        return await call_next(request)
