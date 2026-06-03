"""Tests for anomaly detection: VISITOR_SPIKE, QUEUE_ABANDONMENT_SURGE, DEAD_ZONE, cold-start."""
import uuid
import pytest
import app.db as _db
from app.db import init_db, get_db, insert_event
from app.models import to_canonical, EventBatch
from app.anomalies import get_anomalies


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(_db, "DB_PATH", db)
    init_db(db)
    return db


def _ingest(db, events: list):
    batch = EventBatch(events=events)
    with get_db(db) as conn:
        for raw, ev in zip(events, batch.events):
            insert_event(conn, to_canonical(ev), raw)
        conn.commit()


# ---- event factories --------------------------------------------------------

def _entry(token, ts, staff=False):
    return {"event_type": "entry", "store_code": "store_1008", "id_token": token,
            "camera_id": "CAM_ENTRY_01", "event_timestamp": ts, "is_staff": staff,
            "gender_pred": "F", "age_pred": 25, "age_bucket": "18-24",
            "is_face_hidden": False, "group_id": None, "group_size": None}


def _zone(track_id, zone, name, ts):
    return {"event_type": "zone_entered", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_FLOOR_01", "zone_id": zone, "zone_name": name,
            "zone_type": "SHELF", "is_revenue_zone": "Yes", "event_time": ts,
            "zone_hotspot_x": 100.0, "zone_hotspot_y": 200.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_done(track_id, ts):
    return {"event_type": "queue_completed", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": ts, "queue_exit_ts": ts,
            "wait_seconds": 45, "queue_position_at_join": 1, "abandoned": False,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_aband(track_id, ts):
    return {"event_type": "queue_abandoned", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": None, "queue_exit_ts": ts,
            "wait_seconds": 20, "queue_position_at_join": 3, "abandoned": True,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


# ---- empty / unknown store --------------------------------------------------

def test_no_anomalies_empty_store(tmp_db):
    r = get_anomalies("ST1008", db_path=tmp_db)
    assert r["anomalies"] == []
    assert r["baseline"] == "static"


def test_unknown_store_no_crash(tmp_db):
    r = get_anomalies("STORE_UNKNOWN", db_path=tmp_db)
    assert r["store_id"] == "STORE_UNKNOWN"
    assert r["anomalies"] == []


# ---- cold-start baseline ----------------------------------------------------

def test_cold_start_baseline_when_less_than_3_days(tmp_db):
    # Only 1 day of history → baseline should be "static"
    events = [_entry(f"V{i}", "2026-04-10T10:00:00") for i in range(5)]
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    assert r["baseline"] == "static"


def test_rolling_baseline_when_enough_history(tmp_db):
    # 4 days of history → baseline should be "rolling"
    days = ["2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"]
    events = []
    for day in days:
        for i in range(5):
            ts = f"{day}T10:{i:02d}:00"
            events.append(_entry(f"V{day}_{i}", ts))
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    assert r["baseline"] == "rolling"


# ---- VISITOR_SPIKE ----------------------------------------------------------

def test_visitor_spike_static_threshold(tmp_db):
    """Static baseline: >30 entries in current hour → VISITOR_SPIKE."""
    # 1 day of history (cold start), 35 entries in same hour
    events = [_entry(f"V{i}", "2026-04-10T10:00:00") for i in range(35)]
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "VISITOR_SPIKE" in types


def test_visitor_spike_rolling(tmp_db):
    """Rolling baseline: current hour > 2× avg of same hour over past days."""
    events = []
    # 4 prior days: ~5 entries at 10:xx each
    for day in range(4):
        for i in range(5):
            ts = f"2026-04-0{3+day}T10:{i:02d}:00"
            events.append(_entry(f"H{day}_{i}", ts))
    # Today (day 7) at 10:xx: 25 entries — well above 2×5=10
    for i in range(25):
        events.append(_entry(f"TODAY_{i}", f"2026-04-07T10:{i:02d}:00"))
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "VISITOR_SPIKE" in types


def test_no_visitor_spike_normal_traffic(tmp_db):
    """Normal traffic: no spike despite rolling baseline."""
    events = []
    for day in range(4):
        for i in range(10):
            ts = f"2026-04-0{3+day}T10:{i:02d}:00"
            events.append(_entry(f"H{day}_{i}", ts))
    # Today: 11 entries — below 2×10=20
    for i in range(11):
        events.append(_entry(f"TODAY_{i}", f"2026-04-07T10:{i:02d}:00"))
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "VISITOR_SPIKE" not in types


# ---- QUEUE_ABANDONMENT_SURGE ------------------------------------------------

def test_abandonment_surge_fires(tmp_db):
    """6 abandoned + 2 completed in last 30 min → rate 75% > 50%."""
    ts = "2026-04-10T10:00:00"
    events = (
        [_entry(f"V{i}", ts) for i in range(10)] +
        [_queue_aband(5000 + i, ts) for i in range(6)] +
        [_queue_done(5010 + i, ts) for i in range(2)]
    )
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "QUEUE_ABANDONMENT_SURGE" in types
    surge = next(a for a in r["anomalies"] if a["type"] == "QUEUE_ABANDONMENT_SURGE")
    assert surge["severity"] == "critical"


def test_no_abandonment_surge_below_threshold(tmp_db):
    """3 abandoned + 7 completed → rate 30% < 50% → no surge."""
    ts = "2026-04-10T10:00:00"
    events = (
        [_entry(f"V{i}", ts) for i in range(10)] +
        [_queue_aband(5000 + i, ts) for i in range(3)] +
        [_queue_done(5010 + i, ts) for i in range(7)]
    )
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "QUEUE_ABANDONMENT_SURGE" not in types


def test_abandonment_surge_ignores_old_events(tmp_db):
    """Old abandoned events (outside 30-min window) should not trigger surge."""
    old_ts = "2026-04-10T08:00:00"   # 2 h before latest
    new_ts = "2026-04-10T10:00:00"
    events = (
        [_entry(f"V{i}", new_ts) for i in range(5)] +
        [_queue_aband(5000 + i, old_ts) for i in range(8)] +  # old, outside window
        [_queue_done(5020, new_ts)]                             # 1 recent completed
    )
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "QUEUE_ABANDONMENT_SURGE" not in types


# ---- DEAD_ZONE --------------------------------------------------------------

def test_dead_zone_fires(tmp_db):
    """Zone B has no visits in last 2 h while Zone A does → DEAD_ZONE."""
    recent = "2026-04-10T10:00:00"
    old    = "2026-04-10T07:00:00"   # 3 h before latest event
    events = (
        [_entry(f"V{i}", recent) for i in range(5)] +  # active traffic
        [_zone(5000 + i, "PURPLLE_BLR_1008_Z_ZA", "Zone A", recent) for i in range(3)] +
        [_zone(5010 + i, "PURPLLE_BLR_1008_Z_ZB", "Zone B", old) for i in range(3)]
    )
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "DEAD_ZONE" in types
    dz = next(a for a in r["anomalies"] if a["type"] == "DEAD_ZONE")
    dead_ids = [z["zone_id"] for z in dz["zones"]]
    assert "PURPLLE_BLR_1008_Z_ZB" in dead_ids
    assert "PURPLLE_BLR_1008_Z_ZA" not in dead_ids


def test_dead_zone_no_false_positive_when_all_active(tmp_db):
    """All zones visited recently → no DEAD_ZONE."""
    ts = "2026-04-10T10:00:00"
    events = (
        [_entry(f"V{i}", ts) for i in range(5)] +
        [_zone(5000 + i, "PURPLLE_BLR_1008_Z_ZA", "Zone A", ts) for i in range(3)] +
        [_zone(5010 + i, "PURPLLE_BLR_1008_Z_ZB", "Zone B", ts) for i in range(3)]
    )
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "DEAD_ZONE" not in types


def test_dead_zone_no_false_positive_when_store_quiet(tmp_db):
    """Old zone visits + no recent entries → store is quiet, don't flag dead zones."""
    old = "2026-04-10T07:00:00"
    events = [_zone(5000 + i, "PURPLLE_BLR_1008_Z_ZA", "Zone A", old) for i in range(3)]
    _ingest(tmp_db, events)
    r = get_anomalies("ST1008", db_path=tmp_db)
    types = [a["type"] for a in r["anomalies"]]
    assert "DEAD_ZONE" not in types


# ---- HTTP endpoint ----------------------------------------------------------

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture()
def client(tmp_db, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", tmp_db)
    return TestClient(app)


def test_anomalies_endpoint_unknown_store(client):
    r = client.get("/stores/STORE_BLR_002/anomalies")
    assert r.status_code == 200
    data = r.json()
    assert data["anomalies"] == []
    assert "baseline" in data
