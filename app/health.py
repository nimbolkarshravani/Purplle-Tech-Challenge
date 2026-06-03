"""Health endpoint: per-store last event time + STALE_FEED detection."""
import logging
from datetime import datetime
from pathlib import Path

from app.db import get_db, last_event_times

logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 10


def get_health(db_path: Path = None) -> dict:
    """Return health payload. Never raises -- DB errors return degraded status."""
    try:
        with get_db(db_path) as conn:
            store_times = last_event_times(conn)
    except Exception as exc:
        logger.error(f"Health DB read failed: {exc}")
        return {
            "status": "degraded",
            "error": "db_unavailable",
            "detail": str(exc),
        }

    now = datetime.utcnow()
    stores = {}
    for store_id, last_ts in store_times.items():
        try:
            last_dt = datetime.fromisoformat(last_ts)
            lag_minutes = round((now - last_dt).total_seconds() / 60, 1)
            feed_status = "STALE_FEED" if lag_minutes > STALE_THRESHOLD_MINUTES else "ok"
        except Exception:
            lag_minutes = None
            feed_status = "STALE_FEED"

        stores[store_id] = {
            "last_event_ts": last_ts,
            "lag_minutes": lag_minutes,
            "feed_status": feed_status,
        }

    return {
        "status": "ok",
        "stores": stores,
        "checked_at": now.isoformat(),
    }
