"""FastAPI application -- Store Intelligence API."""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import DB_PATH, init_db
from app.anomalies import get_anomalies
from app.funnel import get_funnel
from app.health import get_health
from app.ingestion import ingest_batch
from app.metrics import get_heatmap, get_metrics

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            entry.update(record.extra)
        return json.dumps(entry)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        logger.info("DB initialised", extra={"event": "startup", "db": str(DB_PATH)})
    except Exception as exc:
        logger.error(f"DB init failed: {exc}", extra={"event": "startup_error"})
    yield


app = FastAPI(title="Store Intelligence API", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request",
        extra={
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


# ---------------------------------------------------------------------------
# Global exception handler -- no raw stack traces
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error": "internal_server_error"},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/events/ingest")
async def ingest(request: Request):
    """
    Ingest a batch of up to 500 events.
    Idempotent by stable event key.
    Returns 200 on full success, 207 on partial, 400 on bad JSON.
    Never returns 5xx for malformed event payloads.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "invalid_json"},
        )

    if isinstance(body, list):
        raw_events = body
    elif isinstance(body, dict) and "events" in body:
        raw_events = body["events"]
    else:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "expected {events:[...]} or a bare list"},
        )

    import app.db as _db
    try:
        result = ingest_batch(raw_events, db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"DB error during ingest: {exc}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": "db_unavailable"},
        )

    status_code = 207 if result["errors"] else 200
    return JSONResponse(status_code=status_code, content=result)


@app.get("/stores/{store_id}/metrics")
async def metrics(store_id: str):
    """Real-time store metrics: visitors, conversion, dwell, queue stats."""
    import app.db as _db
    try:
        return get_metrics(store_id, db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"Metrics failed: {exc}")
        return JSONResponse(status_code=503, content={"status": "error", "error": "db_unavailable"})


@app.get("/stores/{store_id}/funnel")
async def funnel(store_id: str):
    """4-step conversion funnel for the store."""
    import app.db as _db
    try:
        return get_funnel(store_id, db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"Funnel failed: {exc}")
        return JSONResponse(status_code=503, content={"status": "error", "error": "db_unavailable"})


@app.get("/stores/{store_id}/heatmap")
async def heatmap(store_id: str):
    """Zone visit frequency and dwell heatmap, normalised 0-100."""
    import app.db as _db
    try:
        return get_heatmap(store_id, db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"Heatmap failed: {exc}")
        return JSONResponse(status_code=503, content={"status": "error", "error": "db_unavailable"})


@app.get("/stores/{store_id}/anomalies")
async def anomalies(store_id: str):
    """Detect VISITOR_SPIKE, QUEUE_ABANDONMENT_SURGE, DEAD_ZONE anomalies."""
    import app.db as _db
    try:
        return get_anomalies(store_id, db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"Anomalies failed: {exc}")
        return JSONResponse(status_code=503, content={"status": "error", "error": "db_unavailable"})


@app.get("/health")
async def health():
    """Per-store last event time + STALE_FEED warning if lag > 10 min."""
    import app.db as _db
    try:
        return get_health(db_path=_db.DB_PATH)
    except Exception as exc:
        logger.error(f"Health check failed: {exc}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": "db_unavailable"},
        )
