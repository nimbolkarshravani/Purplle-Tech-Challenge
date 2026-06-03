"""Conversion funnel computation for a store."""
from pathlib import Path

from app.db import fetch_events, get_db


def get_funnel(store_id: str, db_path: Path = None) -> dict:
    with get_db(db_path) as conn:
        entries = fetch_events(conn, store_id, ["entry"])
        zone_evts = fetch_events(conn, store_id, ["zone_entered"])
        queue_evts = fetch_events(conn, store_id, ["queue_completed", "queue_abandoned"])

    entered = {e["id_token"] for e in entries if not e.get("is_staff", False)}
    n_entered = len(entered)
    n_zone = len({e["track_id"] for e in zone_evts})
    n_queue = len({e["track_id"] for e in queue_evts})
    n_purchase = len({e["track_id"] for e in queue_evts
                      if e["event_type"] == "queue_completed"})

    def _drop(prev: int, curr: int) -> float:
        return round((prev - curr) / prev * 100, 1) if prev else 0.0

    steps = [
        {"step": "entry",         "label": "Entered Store",        "count": n_entered,  "drop_off_pct": 0.0},
        {"step": "zone_visit",    "label": "Browsed Zones",        "count": n_zone,     "drop_off_pct": _drop(n_entered, n_zone)},
        {"step": "billing_queue", "label": "Joined Billing Queue", "count": n_queue,    "drop_off_pct": _drop(n_zone, n_queue)},
        {"step": "purchase",      "label": "Completed Purchase",   "count": n_purchase, "drop_off_pct": _drop(n_queue, n_purchase)},
    ]

    conversion_rate = round(n_purchase / n_entered, 4) if n_entered else 0.0
    return {"store_id": store_id, "steps": steps, "conversion_rate": conversion_rate}
