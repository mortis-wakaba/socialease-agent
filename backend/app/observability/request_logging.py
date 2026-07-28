"""Structured request logging and slow-request metrics middleware."""

import json
import logging
import os
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.runtime_events import record_slow_request
from app.request_context import REQUEST_ID_HEADER, get_request_id


LOGGER_NAME = "socialease.request"
PROCESS_TIME_HEADER = "X-Process-Time-Ms"


class StructuredRequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit privacy-safe JSON request logs and record slow requests."""

    def __init__(
        self,
        app,
        *,
        slow_request_ms: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(app)
        self.slow_request_ms = (
            float(os.getenv("SOCIALEASE_SLOW_REQUEST_MS", "1000"))
            if slow_request_ms is None
            else slow_request_ms
        )
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    async def dispatch(self, request: Request, call_next) -> Response:
        started = perf_counter()
        status_code = 500
        response: Response | None = None
        error_category: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            error_category = "UNHANDLED_EXCEPTION"
            raise
        finally:
            latency_ms = (perf_counter() - started) * 1000
            request_id = _request_id_for_log(request)
            if response is not None:
                response.headers[PROCESS_TIME_HEADER] = f"{latency_ms:.2f}"
            if latency_ms >= self.slow_request_ms:
                await record_slow_request()
            self.logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "latency_ms": round(latency_ms, 2),
                        "slow": latency_ms >= self.slow_request_ms,
                        "error_category": error_category,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )


def _request_id_for_log(request: Request) -> str:
    """Return the request id without reading body or user content."""
    request_id = (
        getattr(request.state, "request_id", None)
        or get_request_id()
        or request.headers.get(REQUEST_ID_HEADER)
    )
    if request_id:
        return str(request_id)
    return str(uuid4())
