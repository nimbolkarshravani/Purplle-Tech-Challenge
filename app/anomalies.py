"""Anomaly detection for store event streams."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.db import fetch_events, get_db

SPIKE_MULTIPLIER = 2.0
SPIKE_STATIC_THRESHOLD = 30
ABANDONMENT_WINDOW_MINUTES = 30
ABANDONMENT_RATE_THRESHOLD = 0.5
DEAD_ZONE_WINDOW_HOURS = 2
MIN_HISTORY_DAYS = 3


def get_anomalies(store_id: str, db_path: Path = None) -> dict:
    with get_db(db_path) as conn:
        entries = fetch_events(conn, store_id, ["entry"])
        zone_evts = fetch_events(conn, store_id, ["zone_entered"])
        queue_evts = fetch_events(conn, store_id, ["queue_completed", "queue_abandoned"])

    now = _latest_ts(entries, zone_evts, queue_evts) or datetime.now(tz=timezone.utc)
    baseline, history_days = _baseline_type(entries, now)
    anomalies = []
    anomalies += _check_visitor_spike(entries, now, baseline)
    anomalies += _check_abandonment_surge(queue_evts, now)
    anomalies += _check_dead_zones(zone_evts, entries, now)
    return {"store_id": store_id, "baseline": baseline, "history_days": history_days, "anomalies": anomalies}


def _latest_ts(*event_lists) -> Optional[datetime]:
    latest = None
    for evts in event_lists:
        for e in evts:
            raw = e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts")
            if raw:
                try:
                    ts = datetime.fromisoformat(raw)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest is None or ts > latest:
                        latest = ts
                except ValueError:
                    pass
    return latest


def _baseline_type(entries: list, now: datetime) -> tuple[str, int]:
    if not entries:
        return "static", 0
    timestamps = []
    for e in entries:
        raw = e.get("event_timestamp") or e.get("event_time")
        if raw:
            try:
                ts = datetime.fromisoformat(raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                timestamps.append(ts)
            except ValueError:
                pass
    if not timestamps:
        return "static", 0
    oldest = min(timestamps)
    days = (now - oldest).days
    return ("rolling" if days >= MIN_HISTORY_DAYS else "static"), days


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    except ValueError:
        return None


def _check_visitor_spike(entries: list, now: datetime, baseline: str) -> list:
    anomalies = []
    hourly: dict = defaultdict(int)
    for e in entries:
        if e.get("is_staff"):
            continue
        ts = _parse_ts(e.get("event_timestamp") or e.get("event_time"))
        if ts:
            hourly[(ts.date(), ts.hour)] += 1
    if not hourly:
        return []
    current_hour = (now.date(), now.hour)
    current_count = hourly.get(current_hour, 0)
    if baseline == "static":
        if current_count > SPIKE_STATIC_THRESHOLD:
            anomalies.append({"type": "VISITOR_SPIKE", "severity": "warning",
                "detail": f"Current hour has {current_count} entries (static threshold {SPIKE_STATIC_THRESHOLD})",
                "detected_at": now.isoformat()})
        return anomalies
    same_hour_counts = [count for (date, hour), count in hourly.items()
                        if hour == now.hour and date != now.date()]
    if not same_hour_counts:
        return []
    avg = sum(same_hour_counts) / len(same_hour_counts)
    if avg > 0 and current_count > SPIKE_MULTIPLIER * avg:
        anomalies.append({"type": "VISITOR_SPIKE", "severity": "warning",
            "detail": f"Current hour has {current_count} entries ({SPIKE_MULTIPLIER}x rolling avg {avg:.1f})",
            "detected_at": now.isoformat()})
    return anomalies


def _check_abandonment_surge(queue_evts: list, now: datetime) -> list:
    window_start = now - timedelta(minutes=ABANDONMENT_WINDOW_MINUTES)
    recent = [e for e in queue_evts
              if _parse_ts(e.get("queue_join_ts") or e.get("event_time")) is not None
              and _parse_ts(e.get("queue_join_ts") or e.get("event_time")) >= window_start]
    if len(recent) < 2:
        return []
    abandoned = sum(1 for e in recent if e["event_type"] == "queue_abandoned")
    rate = abandoned / len(recent)
    if rate > ABANDONMENT_RATE_THRESHOLD:
        return [{"type": "QUEUE_ABANDONMENT_SURGE", "severity": "critical",
            "detail": f"Abandonment rate {rate:.0%} in last {ABANDONMENT_WINDOW_MINUTES} min ({abandoned}/{len(recent)} events)",
            "detected_at": now.isoformat()}]
    return []


def _check_dead_zones(zone_evts: list, entries: list, now: datetime) -> list:
    window_start = now - timedelta(hours=DEAD_ZONE_WINDOW_HOURS)
    recent_entries = [e for e in entries
        if not e.get("is_staff") and
        _parse_ts(e.get("event_timestamp") or e.get("event_time")) is not None and
        _parse_ts(e.get("event_timestamp") or e.get("event_time")) >= window_start]
    if len(recent_entries) < 3:
        return []
    all_zones: dict = {}
    active_zones: set = set()
    for e in zone_evts:
        zid = e.get("zone_id")
        if not zid:
            continue
        all_zones[zid] = e.get("zone_name", zid)
        ts = _parse_ts(e.get("event_time"))
        if ts and ts >= window_start:
            active_zones.add(zid)
    dead = [{"zone_id": zid, "zone_name": name}
            for zid, name in all_zones.items() if zid not in active_zones]
    if not dead:
        return []
    return [{"type": "DEAD_ZONE", "severity": "info",
        "detail": f"{len(dead)} zone(s) had 0 visits in last {DEAD_ZONE_WINDOW_HOURS}h while store has active traffic: "
            + ", ".join(z["zone_name"] for z in dead[:5]) + ("..." if len(dead) > 5 else ""),
        "zones": dead, "detected_at": now.isoformat()}]
