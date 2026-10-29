"""Structured logging, request IDs, security headers and rate limiting."""
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get() or None,
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STD})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(get_settings().log_level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # our middleware logs requests itself


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

access_log = logging.getLogger("rag.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            return response
        finally:
            access_log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            request_id_var.reset(token)


class RateLimiter:
    """In-memory sliding window. Fine for one process; use Redis if you scale horizontally."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, limit: int, window: float = 60.0) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                return window - (now - q[0])
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory under key churn
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= window]:
                    del self._hits[k]
            return 0.0


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if get_settings().trusted_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return
    client = request.headers.get("x-api-key") or client_ip(request)
    retry = limiter.check(f"{request.url.path}:{client}", limit)
    if retry:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(int(retry) + 1)},
        )
