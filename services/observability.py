"""Low-cardinality metrics and allowlisted, content-free JSON runtime events."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
import time
from uuid import uuid4

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

request_id = ContextVar("request_id", default=None)
HTTP_REQUESTS = Counter("qa_http_requests_total", "Completed HTTP requests", ["method", "route", "status"])
HTTP_SECONDS = Histogram("qa_http_duration_seconds", "HTTP latency", ["route"], buckets=(.05, .1, .3, 1, 3, 10, 30, 90))
INSPECTIONS = Counter("qa_inspections_total", "Completed inspections", ["mode", "outcome"])
INSPECTION_SECONDS = Histogram("qa_inspection_duration_seconds", "Inspection execution latency", ["mode"], buckets=(.1, 1, 5, 15, 30, 60, 120))
WORKER_ACTIVE = Gauge("qa_worker_active", "Active inspection threads")
WORKER_HEARTBEAT = Gauge("qa_worker_heartbeat_timestamp_seconds", "Last successful worker reconciliation")
TENANT_RECONCILE_FAILURES = Gauge("qa_tenant_reconciliation_failures", "Tenant databases failing the latest reconciliation")
QUEUE_ITEMS = Gauge("qa_queue_items", "Durable queue items across configured tenants", ["status"])
QUEUE_AGE = Gauge("qa_queue_oldest_age_seconds", "Age of oldest queued/running/retry item")
JOB_ITEMS = Counter("qa_job_items_total", "Terminal item outcomes", ["outcome", "mode"])
JOB_LATENCY = Histogram("qa_job_end_to_end_seconds", "Enqueue to terminal including retry and queue time", ["mode", "outcome"], buckets=(1, 5, 30, 60, 120, 300, 600, 1800, 3600))
LLM_CALLS = Counter("qa_llm_calls_total", "Logical model requests", ["purpose", "outcome"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        from services.tenancy import tenant_id
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                   "logger": record.name, "event": getattr(record, "event", "runtime_log"),
                   "request_id": request_id.get(), "tenant_id": getattr(record, "tenant_id", tenant_id.get())}
        # Never serialize raw message/args, exception text, SQL, URLs, headers,
        # prompts or model responses. Third-party logs retain logger and level.
        for name in ("job_id", "task_id", "item_id", "duration_ms", "status", "error_code", "route", "mode", "count"):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_info:
            payload["error_code"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # Last-resort exceptions must not bypass the safe formatter with a raw
    # driver traceback that can contain connection URLs or SQL values.
    sys.excepthook = lambda kind, value, tb: logging.getLogger("quality_agent").critical(
        "unhandled", extra={"event": "unhandled_exception", "error_code": kind.__name__})
    import threading
    threading.excepthook = lambda args: logging.getLogger("quality_agent").error(
        "thread_failed", extra={"event": "thread_unhandled_exception", "error_code": args.exc_type.__name__})
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True


def event(name, **fields):
    logging.getLogger("quality_agent").info(name, extra={"event": name, **fields})


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = request_id.set(uuid4().hex)
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id.get()
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            elapsed = time.monotonic() - started
            if route not in {"/metrics", "/health"}:
                HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
                HTTP_SECONDS.labels(route).observe(elapsed)
                event("http_request", route=route, status=status, duration_ms=round(elapsed * 1000),
                      tenant_id=getattr(request.state, "tenant_id", "default"))
            request_id.reset(token)
