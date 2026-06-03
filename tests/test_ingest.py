# PROMPT: Write pytest tests for C1: ingest idempotency (same batch twice -> row
#         count unchanged), partial success on malformed events (2xx + per-event
#         errors), and GET /health returning valid JSON with store data.
# CHANGES MADE: Used db_path fixture with monkeypatch so every test gets an
#               isolated SQLite DB. Verified 207 status + partial body on mixed
#               valid+malformed batch. Added test for bare-list POST body.

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

ENTRY = {
    "event_type": "entry",
    "id_token": "ID_60001",
    "store_code": "store_1076",
    "camera_id": "cam1",
    "event_timestamp": "2026-03-08T18:10:05.120000",
    "is_staff": False,
    "gender_pred": "F",
    "age_pred": 28,
    "age_bucket": "25-34",
    "is_face_hidden": False,
    "group_id": None,
    "group_size": None,
}
ZONE = {
    "event_type": "zone_entered",
    "track_id": 101,
    "store_id": "ST1076",
    "camera_id": "CAM2",
    "zone_id": "PURPLLE_MUM_1076_Z01",
    "zone_name": "Left Shelf",
    "zone_type": "SHELF",
    "is_revenue_zone": "Yes",
    "event_time": "2026-03-08T18:10:45.280000",
    "zone_hotspot_x": 412.6,
    "zone_hotspot_y": 238.4,
    "gender": "F",
    "age": 28,
    "age_bucket": "25-34",
}
QUEUE = {
    "queue_event_id": "cfd8e3c5-7aa0-4ea3-9b59-692d50da8308",
    "event_type": "queue_completed",
    "track_id": 102,
    "store_id": "ST1076",
    "camera_id": "PURPLLE_MUM_1076_CAM6",
    "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
    "zone_name": "Billing Counter Queue",
    "zone_type": "BILLING",
    "is_revenue_zone": "Yes",
    "queue_join_ts": "2026-03-08T18:13:05.080000",
    "queue_served_ts": "2026-03-08T18:13:13.240000",
    "queue_exit_ts": "2026-03-08T18:15:31.840000",
    "wait_seconds": 8,
    "queue_position_at_join": 2,
    "abandoned": False,
    "zone_hotspot_x": 602.8,
    "zone_hotspot_y": 183.4,
    "gender": "M",
    "age": 31,
    "age_bucket": "25-34",
}
MALFORMED_MISSING_FIELDS = {"event_type": "entry", "id_token": "X"}
MALFORMED_UNKNOWN_TYPE = {"event_type": "reentry", "foo": "bar"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with an isolated per-test SQLite DB."""
    db = tmp_path / "test.db"
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db)
    db_mod.init_db(db)
    from app.main import app as fastapi_app
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_first_ingest_accepts_three(client):
    r = client.post("/events/ingest", json={"events": [ENTRY, ZONE, QUEUE]})
    assert r.status_code in (200, 207)
    assert r.json()["accepted"] == 3


def test_second_ingest_of_same_batch_has_zero_accepted(client):
    batch = {"events": [ENTRY, ZONE, QUEUE]}
    client.post("/events/ingest", json=batch)
    r2 = client.post("/events/ingest", json=batch)
    body = r2.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 3


def test_row_count_unchanged_after_duplicate_ingest(tmp_path, monkeypatch):
    db = tmp_path / "rc.db"
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db)
    db_mod.init_db(db)
    from app.main import app as fastapi_app
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        c.post("/events/ingest", json={"events": [ENTRY]})
        c.post("/events/ingest", json={"events": [ENTRY]})
    with db_mod.get_db(db) as conn:
        assert db_mod.event_count(conn) == 1


# ---------------------------------------------------------------------------
# Partial success on malformed events
# ---------------------------------------------------------------------------

def test_partial_success_mixed_batch(client):
    batch = {"events": [ENTRY, MALFORMED_MISSING_FIELDS, ZONE, MALFORMED_UNKNOWN_TYPE]}
    r = client.post("/events/ingest", json=batch)
    assert r.status_code == 207
    body = r.json()
    assert body["status"] == "partial"
    assert body["accepted"] == 2
    assert len(body["errors"]) == 2


def test_errors_carry_index(client):
    batch = {"events": [ENTRY, MALFORMED_MISSING_FIELDS, ZONE, MALFORMED_UNKNOWN_TYPE]}
    body = client.post("/events/ingest", json=batch).json()
    assert body["errors"][0]["index"] == 1
    assert body["errors"][1]["index"] == 3


def test_all_malformed_never_5xx(client):
    batch = {"events": [MALFORMED_MISSING_FIELDS, MALFORMED_UNKNOWN_TYPE]}
    r = client.post("/events/ingest", json=batch)
    assert r.status_code < 500
    body = r.json()
    assert body["accepted"] == 0
    assert len(body["errors"]) == 2


def test_error_has_reason_field(client):
    r = client.post("/events/ingest", json={"events": [MALFORMED_MISSING_FIELDS]})
    assert "reason" in r.json()["errors"][0]


def test_empty_batch_returns_ok(client):
    r = client.post("/events/ingest", json={"events": []})
    assert r.status_code in (200, 207)
    assert r.json()["accepted"] == 0


def test_bare_list_body_accepted(client):
    r = client.post("/events/ingest", json=[ENTRY])
    assert r.status_code in (200, 207)
    assert r.json()["accepted"] == 1


def test_invalid_json_body_returns_400(client):
    r = client.post(
        "/events/ingest",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_has_status_ok(client):
    r = client.get("/health")
    assert r.json()["status"] == "ok"


def test_health_has_stores_and_checked_at(client):
    r = client.get("/health")
    body = r.json()
    assert "stores" in body
    assert "checked_at" in body


def test_health_shows_store_after_ingest(client):
    client.post("/events/ingest", json={"events": [ENTRY]})
    body = client.get("/health").json()
    assert "ST1076" in body["stores"]


def test_health_store_has_feed_status(client):
    client.post("/events/ingest", json={"events": [ENTRY]})
    store = client.get("/health").json()["stores"]["ST1076"]
    assert "feed_status" in store
    assert "last_event_ts" in store


def test_health_empty_db_has_no_stores(client):
    body = client.get("/health").json()
    assert body["stores"] == {}


# ---------------------------------------------------------------------------
# All three families ingest cleanly
# ---------------------------------------------------------------------------

def test_all_three_families(client):
    r = client.post("/events/ingest", json={"events": [ENTRY, ZONE, QUEUE]})
    body = r.json()
    assert body["accepted"] == 3
    assert body["errors"] == []
