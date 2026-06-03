"""Tests for get_funnel."""
import uuid
import pytest
import app.db as _db
from app.db import init_db, get_db, insert_event
from app.models import to_canonical, EventBatch
from app.funnel import get_funnel


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


def _entry(token, ts="2026-04-10T10:00:00", staff=False):
    return {"event_type": "entry", "store_code": "store_1008", "id_token": token,
            "camera_id": "CAM_ENTRY_01", "event_timestamp": ts, "is_staff": staff,
            "gender_pred": "F", "age_pred": 25, "age_bucket": "18-24", "is_face_hidden": False,
            "group_id": None, "group_size": None}


def _zone(track_id, ts="2026-04-10T10:00:00"):
    return {"event_type": "zone_entered", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_FLOOR_01", "zone_id": "PURPLLE_BLR_1008_Z_Z01",
            "zone_name": "Shelf", "zone_type": "SHELF", "is_revenue_zone": "Yes",
            "event_time": ts, "zone_hotspot_x": 100.0, "zone_hotspot_y": 200.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_done(track_id, ts="2026-04-10T10:00:00"):
    return {"event_type": "queue_completed", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": ts, "queue_exit_ts": ts,
            "wait_seconds": 45, "queue_position_at_join": 1, "abandoned": False,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_aband(track_id, ts="2026-04-10T10:00:00"):
    return {"event_type": "queue_abandoned", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": None, "queue_exit_ts": ts,
            "wait_seconds": 20, "queue_position_at_join": 3, "abandoned": True,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def test_empty_store_returns_zero_conversion(tmp_db):
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["conversion_rate"] == 0.0
    assert all(s["count"] == 0 for s in r["steps"])

def test_unknown_store_no_crash(tmp_db):
    r = get_funnel("STORE_UNKNOWN", db_path=tmp_db)
    assert r["store_id"] == "STORE_UNKNOWN"
    assert r["conversion_rate"] == 0.0

def test_full_funnel_counts(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B"), _entry("C"),
                     _zone(5001), _zone(5002),
                     _queue_done(5001), _queue_aband(5002)])
    r = get_funnel("ST1008", db_path=tmp_db)
    steps = {s["step"]: s for s in r["steps"]}
    assert steps["entry"]["count"] == 3
    assert steps["zone_visit"]["count"] == 2
    assert steps["billing_queue"]["count"] == 2
    assert steps["purchase"]["count"] == 1

def test_reentry_counts_once(tmp_db):
    _ingest(tmp_db, [_entry("A", ts="2026-04-10T09:00:00"),
                     _entry("A", ts="2026-04-10T14:00:00"), _entry("B")])
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["steps"][0]["count"] == 2

def test_staff_excluded_from_funnel(tmp_db):
    _ingest(tmp_db, [_entry("STAFF", staff=True), _entry("V1")])
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["steps"][0]["count"] == 1

def test_zero_purchase_no_crash(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B"), _zone(5001), _queue_aband(5001)])
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["steps"][3]["count"] == 0
    assert r["conversion_rate"] == 0.0

def test_drop_off_pct_first_step_is_zero(tmp_db):
    _ingest(tmp_db, [_entry("A")])
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["steps"][0]["drop_off_pct"] == 0.0

def test_drop_off_pct_calculation(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B"), _entry("C"), _entry("D"),
                     _zone(5001), _zone(5002), _queue_done(5001)])
    r = get_funnel("ST1008", db_path=tmp_db)
    steps = {s["step"]: s for s in r["steps"]}
    assert steps["zone_visit"]["drop_off_pct"] == pytest.approx(50.0)
    assert steps["billing_queue"]["drop_off_pct"] == pytest.approx(50.0)

def test_conversion_rate_calculation(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B"), _entry("C"), _entry("D"), _queue_done(5001)])
    r = get_funnel("ST1008", db_path=tmp_db)
    assert r["conversion_rate"] == pytest.approx(1/4, rel=1e-3)

def test_funnel_structure(tmp_db):
    r = get_funnel("ST1008", db_path=tmp_db)
    assert "store_id" in r
    assert "steps" in r
    assert "conversion_rate" in r
    assert len(r["steps"]) == 4
    for s in r["steps"]:
        assert {"step", "label", "count", "drop_off_pct"} <= s.keys()

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture()
def client(tmp_db, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", tmp_db)
    return TestClient(app)

def test_funnel_endpoint_unknown_store(client):
    r = client.get("/stores/STORE_BLR_002/funnel")
    assert r.status_code == 200
    data = r.json()
    assert data["steps"][0]["count"] == 0
    assert data["conversion_rate"] == 0.0
