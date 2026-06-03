"""Tests for get_metrics and get_heatmap."""
import pytest
import app.db as _db
from app.db import init_db, get_db, insert_event
from app.models import to_canonical, EventBatch
from app.metrics import get_metrics, get_heatmap


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


def _zone(track_id, zone="PURPLLE_BLR_1008_Z_Z01", name="Shelf A", ts="2026-04-10T10:00:00"):
    return {"event_type": "zone_entered", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_FLOOR_01", "zone_id": zone, "zone_name": name,
            "zone_type": "SHELF", "is_revenue_zone": "Yes",
            "event_time": ts, "zone_hotspot_x": 100.0, "zone_hotspot_y": 200.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _zone_exit(track_id, zone="PURPLLE_BLR_1008_Z_Z01", ts="2026-04-10T10:05:00"):
    return {"event_type": "zone_exited", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_FLOOR_01", "zone_id": zone, "zone_name": "Shelf A",
            "zone_type": "SHELF", "is_revenue_zone": "Yes",
            "event_time": ts, "zone_hotspot_x": 100.0, "zone_hotspot_y": 200.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_done(track_id, ts="2026-04-10T10:00:00", wait=60):
    import uuid
    return {"event_type": "queue_completed", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": ts, "queue_exit_ts": ts,
            "wait_seconds": wait, "queue_position_at_join": 1, "abandoned": False,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def _queue_aband(track_id, ts="2026-04-10T10:00:00", wait=30):
    import uuid
    return {"event_type": "queue_abandoned", "store_id": "ST1008", "track_id": track_id,
            "camera_id": "CAM_BILLING_01", "queue_event_id": str(uuid.uuid4()),
            "zone_id": "PURPLLE_BLR_1008_Z_BILLING", "zone_name": "Billing Counter",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": ts, "queue_served_ts": None, "queue_exit_ts": ts,
            "wait_seconds": wait, "queue_position_at_join": 3, "abandoned": True,
            "zone_hotspot_x": 590.0, "zone_hotspot_y": 94.0,
            "gender": "F", "age": 25, "age_bucket": "18-24"}


def test_unknown_store_returns_zeroed(tmp_db):
    r = get_metrics("STORE_UNKNOWN", db_path=tmp_db)
    assert r["unique_visitors"] == 0
    assert r["conversion_rate"] == 0.0
    assert r["queue_stats"]["completed"] == 0

def test_basic_metrics(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B"), _entry("C"),
                     _queue_done(5001), _queue_aband(5002)])
    r = get_metrics("ST1008", db_path=tmp_db)
    assert r["unique_visitors"] == 3
    assert r["queue_stats"]["completed"] == 1
    assert r["queue_stats"]["abandoned"] == 1
    assert r["queue_stats"]["abandonment_rate"] == 0.5
    assert r["conversion_rate"] == pytest.approx(1/3, rel=1e-3)

def test_staff_excluded(tmp_db):
    _ingest(tmp_db, [_entry("STAFF1", staff=True), _entry("V1")])
    r = get_metrics("ST1008", db_path=tmp_db)
    assert r["unique_visitors"] == 1

def test_reentry_dedup(tmp_db):
    _ingest(tmp_db, [_entry("A", ts="2026-04-10T09:00:00"),
                     _entry("A", ts="2026-04-10T11:00:00")])
    r = get_metrics("ST1008", db_path=tmp_db)
    assert r["unique_visitors"] == 1

def test_zero_purchase_store(tmp_db):
    _ingest(tmp_db, [_entry("A"), _entry("B")])
    r = get_metrics("ST1008", db_path=tmp_db)
    assert r["conversion_rate"] == 0.0
    assert r["queue_stats"]["completed"] == 0

def test_dwell_computed(tmp_db):
    _ingest(tmp_db, [_zone(5001, ts="2026-04-10T10:00:00"),
                     _zone_exit(5001, ts="2026-04-10T10:03:00"), _entry("V1")])
    r = get_metrics("ST1008", db_path=tmp_db)
    zid = "PURPLLE_BLR_1008_Z_Z01"
    assert zid in r["avg_dwell_per_zone"]
    assert r["avg_dwell_per_zone"][zid]["avg_dwell_seconds"] == pytest.approx(180.0)

def test_avg_wait_seconds(tmp_db):
    _ingest(tmp_db, [_entry("V1"), _queue_done(5001, wait=100), _queue_done(5002, wait=200)])
    r = get_metrics("ST1008", db_path=tmp_db)
    assert r["queue_stats"]["avg_wait_seconds"] == pytest.approx(150.0)

def test_heatmap_unknown_store(tmp_db):
    r = get_heatmap("STORE_UNKNOWN", db_path=tmp_db)
    assert r["zones"] == []
    assert r["unique_sessions"] == 0

def test_heatmap_low_confidence(tmp_db):
    events = [_zone(5000+i) for i in range(5)]
    exits = [_zone_exit(5000+i) for i in range(5)]
    _ingest(tmp_db, events + exits)
    r = get_heatmap("ST1008", db_path=tmp_db)
    assert r["data_confidence"] == "low"
    assert r["unique_sessions"] == 5

def test_heatmap_ok_confidence(tmp_db):
    events = [_zone(5000+i) for i in range(20)]
    exits = [_zone_exit(5000+i) for i in range(20)]
    _ingest(tmp_db, events + exits)
    r = get_heatmap("ST1008", db_path=tmp_db)
    assert r["data_confidence"] == "ok"
    assert r["unique_sessions"] == 20

def test_heatmap_normalized(tmp_db):
    z1 = "PURPLLE_BLR_1008_Z_Z01"
    z2 = "PURPLLE_BLR_1008_Z_Z02"
    events = ([_zone(5000+i, zone=z1, name="A") for i in range(10)] +
              [_zone(5010+i, zone=z2, name="B") for i in range(5)] +
              [_zone_exit(5000+i, zone=z1) for i in range(10)] +
              [_zone_exit(5010+i, zone=z2) for i in range(5)])
    _ingest(tmp_db, events)
    r = get_heatmap("ST1008", db_path=tmp_db)
    z_map = {z["zone_id"]: z for z in r["zones"]}
    assert z_map[z1]["frequency_normalized"] == 100
    assert z_map[z2]["frequency_normalized"] == 50

from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture()
def client(tmp_db, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", tmp_db)
    return TestClient(app)

def test_metrics_endpoint_unknown_store(client):
    r = client.get("/stores/STORE_BLR_002/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0

def test_heatmap_endpoint_unknown_store(client):
    r = client.get("/stores/STORE_BLR_002/heatmap")
    assert r.status_code == 200
    assert r.json()["zones"] == []
