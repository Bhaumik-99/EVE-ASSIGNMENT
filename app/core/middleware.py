import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.observability import client_ip, rate_limiter

logger = logging.getLogger("diagnostic_booking.http")


async def request_context_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "unhandled_request_error",
            extra={"request_id": request.state.request_id, "method": request.method, "path": request.url.path, "client_ip": client_ip(request)},
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request.state.request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request.state.request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip(request),
        },
    )
    return response


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1/auth/"):
        limit = settings.auth_rate_limit_per_minute
        bucket = "auth"
    elif path == "/api/v1/payments/webhook":
        limit = settings.webhook_rate_limit_per_minute
        bucket = "webhook"
    else:
        return await call_next(request)

    key = (bucket, client_ip(request))
    if not rate_limiter.allow(key, limit):
        response = JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        response.headers["Retry-After"] = "60"
        response.headers["X-Request-ID"] = getattr(request.state, "request_id", "unknown")
        return response
    return await call_next(request)
