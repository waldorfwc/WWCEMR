"""Per-request context plumbing — request_id stored in a contextvar so
every log line can be correlated back to the originating HTTP request.

Wiring:
  - request_id_middleware:    reads X-Cloud-Trace-Context (Cloud Run /
                              GCP injects this) or X-Request-ID, falls
                              back to a fresh UUID; stashes the result
                              on request.state AND in the contextvar,
                              echoes it back as X-Request-ID.
  - RequestIdLogFilter:       logging.Filter that adds request_id to
                              every LogRecord so the default formatter
                              can surface it.

The contextvar lives outside the request lifecycle so background
threads (fax_poller, etc.) get a stable "no-request" sentinel rather
than leaking the previous request's id.

(Fable design review note 11.)
"""
from __future__ import annotations

import contextvars
import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


_NO_REQUEST = "-"

# Module-level contextvar. Default value is the sentinel so any log line
# emitted outside a request still has a value to print.
_current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=_NO_REQUEST,
)


def current_request_id() -> str:
    """Look up the request id for the current async task. Returns the
    "-" sentinel when called outside an HTTP request (e.g. from a
    background scheduler tick)."""
    return _current_request_id.get()


# Cloud Run forwards `X-Cloud-Trace-Context: <trace_id>/<span_id>;o=1`.
# We only need the trace id portion as a correlation handle.
_CLOUD_TRACE_RE = re.compile(r"^([a-f0-9]+)/")


def _extract_id(request: Request) -> str:
    cloud_trace = request.headers.get("x-cloud-trace-context")
    if cloud_trace:
        m = _CLOUD_TRACE_RE.match(cloud_trace)
        if m:
            return m.group(1)
        # No span suffix? Just take the whole header value (defensive).
        return cloud_trace.split(";", 1)[0].strip()
    provided = request.headers.get("x-request-id")
    if provided:
        return provided
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Set the request_id contextvar for the duration of the request +
    surface it on the response so clients can log it too."""

    async def dispatch(self, request, call_next):
        rid = _extract_id(request)
        token = _current_request_id.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
        finally:
            _current_request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


class RequestIdLogFilter(logging.Filter):
    """Inject the current request_id onto every LogRecord. Works
    regardless of which logger emits the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


def install_request_id_logging() -> None:
    """Attach RequestIdLogFilter to every existing root handler and
    update the format to surface request_id. Filters live on handlers
    (not on the root logger) because filters on a non-leaf logger
    don't see records propagated from child loggers. Idempotent —
    re-installing on the same handler is a no-op.
    """
    fmt = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s"
    root = logging.getLogger()
    for h in root.handlers:
        # Attach the filter (skip if already present)
        if not any(isinstance(f, RequestIdLogFilter) for f in h.filters):
            h.addFilter(RequestIdLogFilter())
        # Refresh the formatter on StreamHandlers
        if isinstance(h, logging.StreamHandler):
            h.setFormatter(logging.Formatter(fmt))
