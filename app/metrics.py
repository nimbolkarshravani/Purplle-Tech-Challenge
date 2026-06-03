"""Metrics and heatmap computation for a store."""
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.db import fetch_events, get_db


def _zeroed(store_id: str) -> dict:
    return {
        "store_id": store_id,
        "unique_visitors": 0,
        "conversion_rate": 0.0,
        "avg_dwell_per_zone": {},
        "queue_stats": {"completed": 0, "abandoned": 0, "abandonment_rate": 0.0, "avg_wait_seconds": 0.0},
    }


def get_metrics(store_id: str, db_path: Path = None) -> dict:
    with get_db(db_path) as conn:
        entries = fetch_events(conn, store_id, ["entry"])
        zone_entries = fetch_events(conn, store_id, ["zone_entered"])
        zone_exits = fetch_events(conn, store_id, ["zone_exited"])
        queue_evts = fetch_events(conn, store_id, ["queue_completed", "queue_abandoned"])

    if not entries:
        return _zeroed(store_id)

    visitor_ids = {e["id_token"] for e in entries if not e.get("is_staff", False)}
    n_visitors = len(visitor_ids)
    if n_visitors == 0:
        return _zeroed(store_id)

    completed = {e["track_id"] for e in queue_evts if e["event_type"] == "queue_completed"}
    abandoned = {e["track_id"] for e in queue_evts if e["event_type"] == "queue_abandoned"}
    n_completed = len(completed)
    n_abandoned = len(abandoned)
    total_q = n_completed + n_abandoned
    all_waits = [e.get("wait_seconds", 0) for e in queue_evts]
    avg_wait = round(sum(all_waits) / len(all_waits), 1) if all_waits else 0.0
    conversion_rate = round(n_completed / n_visitors, 4) if n_visitors else 0.0
    abandonment_rate = round(n_abandoned / total_q, 4) if total_q else 0.0
    dwell = _compute_dwell(zone_entries, zone_exits)

    return {
        "store_id": store_id,
        "unique_visitors": n_visitors,
        "conversion_rate": conversion_rate,
        "avg_dwell_per_zone": dwell,
        "queue_stats": {
            "completed": n_completed,
            "abandoned": n_abandoned,
            "abandonment_rate": abandonment_rate,
            "avg_wait_seconds": avg_wait,
        },
    }


def _compute_dwell(zone_entries: list, zone_exits: list) -> dict:
    exits_by_key: dict = defaultdict(list)
    for e in zone_exits:
        ts = datetime.fromisoformat(e["event_time"])
        exits_by_key[(e["track_id"], e["zone_id"])].append(ts)
    for v in exits_by_key.values():
        v.sort()

    dwells: dict = defaultdict(list)
    zone_meta: dict = {}
    for e in zone_entries:
        key = (e["track_id"], e["zone_id"])
        zid = e["zone_id"]
        enter_ts = datetime.fromisoformat(e["event_time"])
        zone_meta[zid] = e.get("zone_name", zid)
        future = [t for t in exits_by_key.get(key, []) if t > enter_ts]
        if future:
            secs = (min(future) - enter_ts).total_seconds()
            if 0 < secs < 7200:
                dwells[zid].append(secs)

    return {
        zid: {"zone_name": zone_meta[zid], "avg_dwell_seconds": round(sum(dw)/len(dw),1), "visit_count": len(dw)}
        for zid, dw in dwells.items()
    }


def get_heatmap(store_id: str, db_path: Path = None) -> dict:
    with get_db(db_path) as conn:
        zone_entries = fetch_events(conn, store_id, ["zone_entered"])
        zone_exits = fetch_events(conn, store_id, ["zone_exited"])

    unique_sessions = len({e["track_id"] for e in zone_entries})
    confidence = "ok" if unique_sessions >= 20 else "low"
    dwell = _compute_dwell(zone_entries, zone_exits)
    if not dwell:
        return {"store_id": store_id, "data_confidence": confidence, "unique_sessions": unique_sessions, "zones": []}

    freq: dict = defaultdict(int)
    zone_names: dict = {}
    for e in zone_entries:
        freq[e["zone_id"]] += 1
        zone_names[e["zone_id"]] = e.get("zone_name", e["zone_id"])

    max_freq = max(freq.values(), default=1)
    max_dwell = max((v["avg_dwell_seconds"] for v in dwell.values()), default=1)
    zones = []
    for zid, d in dwell.items():
        f = freq.get(zid, 0)
        zones.append({
            "zone_id": zid,
            "zone_name": zone_names.get(zid, zid),
            "visit_count": f,
            "frequency_normalized": round(f / max_freq * 100),
            "avg_dwell_seconds": d["avg_dwell_seconds"],
            "dwell_normalized": round(d["avg_dwell_seconds"] / max_dwell * 100),
        })
    zones.sort(key=lambda z: z["frequency_normalized"], reverse=True)
    return {"store_id": store_id, "data_confidence": confidence, "unique_sessions": unique_sessions, "zones": zones}
