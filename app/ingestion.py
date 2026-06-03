"""Event ingestion: validate, normalise, deduplicate, persist."""
import logging
from pathlib import Path

from pydantic import ValidationError

from app.db import get_db, insert_event
from app.models import EventBatch, to_canonical

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 500


def ingest_batch(raw_events: list, db_path: Path = None) -> dict:
    if len(raw_events) > MAX_BATCH_SIZE:
        return {
            "status": "error",
            "message": f"Batch size {len(raw_events)} exceeds limit {MAX_BATCH_SIZE}",
            "accepted": 0,
            "duplicates": 0,
            "errors": [{"index": None, "reason": "batch_too_large"}],
            "total": len(raw_events),
        }

    accepted = 0
    duplicates = 0
    errors = []

    with get_db(db_path) as conn:
        for i, raw in enumerate(raw_events):
            try:
                event = _parse_one(raw)
                canonical = to_canonical(event)
                if insert_event(conn, canonical, raw):
                    accepted += 1
                else:
                    duplicates += 1
            except ValidationError as exc:
                errors.append({
                    "index": i,
                    "reason": "validation_error",
                    "details": exc.errors(include_url=False),
                })
            except Exception as exc:
                errors.append({
                    "index": i,
                    "reason": "parse_error",
                    "details": str(exc),
                })
        conn.commit()

    status = "ok" if not errors else ("partial" if accepted > 0 else "error")
    return {
        "status": status,
        "accepted": accepted,
        "duplicates": duplicates,
        "errors": errors,
        "total": accepted + duplicates + len(errors),
    }


def _parse_one(raw: dict):
    return EventBatch(events=[raw]).events[0]
