# PROMPT: Write pytest tests for app/models.py covering all three event families,
#         discriminated-union parsing, schema round-trip, store-code normalizer,
#         and canonical event conversion.
# CHANGES MADE: Added no-reentry-type guard, cold-start sanity import check,
#               and verified canonical fields map correctly for all three families.

import json
import pytest
from pydantic import ValidationError

from app.models import (
    CanonicalEvent,
    EntryExitEvent,
    EventBatch,
    QueueEvent,
    ZoneEvent,
    normalize_store_id,
    store_id_to_code,
    to_canonical,
)

# ---------------------------------------------------------------------------
# Sample payloads (copied verbatim from sample_eventsbe42122.jsonl)
# ---------------------------------------------------------------------------
ENTRY_RAW = '{"event_type":"entry","id_token":"ID_60001","store_code":"store_1076","camera_id":"cam1","event_timestamp":"2026-03-08T18:10:05.120000","is_staff":false,"gender_pred":"F","age_pred":28,"age_bucket":"25-34","is_face_hidden":false,"group_id":null,"group_size":null}'
EXIT_RAW  = '{"event_type":"exit","id_token":"ID_60001","store_code":"store_1076","camera_id":"cam1","event_timestamp":"2026-03-08T18:12:44.360000","is_staff":false,"gender_pred":"F","age_pred":28,"age_bucket":"25-34","is_face_hidden":false,"group_id":null,"group_size":null}'
ZONE_RAW  = '{"event_type":"zone_entered","track_id":101,"store_id":"ST1076","camera_id":"CAM2","zone_id":"PURPLLE_MUM_1076_Z01","zone_name":"Left Shelf","zone_type":"SHELF","is_revenue_zone":"Yes","event_time":"2026-03-08T18:10:45.280000","zone_hotspot_x":412.6,"zone_hotspot_y":238.4,"gender":"F","age":28,"age_bucket":"25-34"}'
ZONE_EXIT_RAW = '{"event_type":"zone_exited","track_id":101,"store_id":"ST1076","camera_id":"CAM2","zone_id":"PURPLLE_MUM_1076_Z01","zone_name":"Left Shelf","zone_type":"SHELF","is_revenue_zone":"Yes","event_time":"2026-03-08T18:11:18.720000","zone_hotspot_x":418.2,"zone_hotspot_y":241.0,"gender":"F","age":28,"age_bucket":"25-34"}'
QUEUE_RAW = '{"queue_event_id":"cfd8e3c5-7aa0-4ea3-9b59-692d50da8308","event_type":"queue_completed","track_id":102,"store_id":"ST1076","camera_id":"PURPLLE_MUM_1076_CAM6","zone_id":"PURPLLE_MUM_1076_Z_BILLING_01","zone_name":"Billing Counter Queue","zone_type":"BILLING","is_revenue_zone":"Yes","queue_join_ts":"2026-03-08T18:13:05.080000","queue_served_ts":"2026-03-08T18:13:13.240000","queue_exit_ts":"2026-03-08T18:15:31.840000","wait_seconds":8,"queue_position_at_join":2,"abandoned":false,"zone_hotspot_x":602.8,"zone_hotspot_y":183.4,"gender":"M","age":31,"age_bucket":"25-34"}'
ABAND_RAW = '{"queue_event_id":"a1e5c1d3-9e14-4df1-bd2c-4ab5cbf55f91","event_type":"queue_abandoned","track_id":101,"store_id":"ST1076","camera_id":"PURPLLE_MUM_1076_CAM6","zone_id":"PURPLLE_MUM_1076_Z_BILLING_01","zone_name":"Billing Counter Queue","zone_type":"BILLING","is_revenue_zone":"Yes","queue_join_ts":"2026-03-08T18:12:58.240000","queue_served_ts":null,"queue_exit_ts":"2026-03-08T18:14:02.880000","wait_seconds":65,"queue_position_at_join":4,"abandoned":true,"zone_hotspot_x":598.1,"zone_hotspot_y":176.8,"gender":"F","age":28,"age_bucket":"25-34"}'


# ---------------------------------------------------------------------------
# Family 1: entry / exit
# ---------------------------------------------------------------------------

def test_entry_parses():
    e = EntryExitEvent.model_validate_json(ENTRY_RAW)
    assert e.event_type == "entry"
    assert e.id_token == "ID_60001"
    assert e.is_staff is False
    assert e.gender_pred == "F"
    assert e.age_pred == 28
    assert e.group_id is None


def test_exit_parses():
    e = EntryExitEvent.model_validate_json(EXIT_RAW)
    assert e.event_type == "exit"


def test_entry_round_trip():
    e = EntryExitEvent.model_validate_json(ENTRY_RAW)
    e2 = EntryExitEvent.model_validate(e.model_dump())
    assert e2.id_token == e.id_token
    assert e2.event_timestamp == e.event_timestamp


# ---------------------------------------------------------------------------
# Family 2: zone_entered / zone_exited
# ---------------------------------------------------------------------------

def test_zone_entered_parses():
    e = ZoneEvent.model_validate_json(ZONE_RAW)
    assert e.event_type == "zone_entered"
    assert e.track_id == 101
    assert e.zone_hotspot_x == 412.6
    assert e.is_revenue_zone == "Yes"


def test_zone_exited_parses():
    e = ZoneEvent.model_validate_json(ZONE_EXIT_RAW)
    assert e.event_type == "zone_exited"
    assert e.track_id == 101


def test_zone_round_trip():
    e = ZoneEvent.model_validate_json(ZONE_RAW)
    e2 = ZoneEvent.model_validate(e.model_dump())
    assert e2.track_id == e.track_id
    assert e2.event_time == e.event_time


# ---------------------------------------------------------------------------
# Family 3: queue_completed / queue_abandoned
# ---------------------------------------------------------------------------

def test_queue_completed_parses():
    e = QueueEvent.model_validate_json(QUEUE_RAW)
    assert e.event_type == "queue_completed"
    assert e.abandoned is False
    assert e.wait_seconds == 8
    assert e.queue_served_ts is not None


def test_queue_abandoned_parses():
    e = QueueEvent.model_validate_json(ABAND_RAW)
    assert e.event_type == "queue_abandoned"
    assert e.abandoned is True
    assert e.queue_served_ts is None
    assert e.wait_seconds == 65


def test_queue_round_trip():
    e = QueueEvent.model_validate_json(QUEUE_RAW)
    e2 = QueueEvent.model_validate(e.model_dump())
    assert e2.queue_event_id == e.queue_event_id
    assert e2.queue_exit_ts == e.queue_exit_ts


# ---------------------------------------------------------------------------
# EventBatch discriminated union
# ---------------------------------------------------------------------------

def test_event_batch_all_families():
    batch = EventBatch(events=[
        json.loads(ENTRY_RAW),
        json.loads(ZONE_RAW),
        json.loads(QUEUE_RAW),
    ])
    assert len(batch.events) == 3
    assert isinstance(batch.events[0], EntryExitEvent)
    assert isinstance(batch.events[1], ZoneEvent)
    assert isinstance(batch.events[2], QueueEvent)


def test_event_batch_with_exit_and_abandoned():
    batch = EventBatch(events=[
        json.loads(EXIT_RAW),
        json.loads(ZONE_EXIT_RAW),
        json.loads(ABAND_RAW),
    ])
    assert len(batch.events) == 3


# ---------------------------------------------------------------------------
# Store-code normalizer
# ---------------------------------------------------------------------------

def test_normalize_store_id_lowercase():
    assert normalize_store_id("store_1076") == "ST1076"
    assert normalize_store_id("store_1008") == "ST1008"


def test_normalize_store_id_already_st():
    assert normalize_store_id("ST1076") == "ST1076"
    assert normalize_store_id("ST1008") == "ST1008"


def test_normalize_store_id_mixed_case():
    assert normalize_store_id("STORE_1008") == "ST1008"
    assert normalize_store_id("Store_1008") == "ST1008"


def test_store_id_to_code():
    assert store_id_to_code("ST1076") == "store_1076"
    assert store_id_to_code("ST1008") == "store_1008"


def test_roundtrip_store_codes():
    from app.models import normalize_store_id, store_id_to_code
    assert normalize_store_id(store_id_to_code("ST1008")) == "ST1008"
    assert store_id_to_code(normalize_store_id("store_1008")) == "store_1008"


# ---------------------------------------------------------------------------
# Canonical event conversion
# ---------------------------------------------------------------------------

def test_canonical_from_entry():
    e = EntryExitEvent.model_validate_json(ENTRY_RAW)
    c = to_canonical(e)
    assert isinstance(c, CanonicalEvent)
    assert c.store_id == "ST1076"
    assert c.visitor_id == "ID_60001"
    assert c.event_type == "entry"
    assert c.timestamp is not None
    assert c.is_staff is False
    assert c.gender == "F"
    assert c.age == 28


def test_canonical_from_zone():
    e = ZoneEvent.model_validate_json(ZONE_RAW)
    c = to_canonical(e)
    assert c.store_id == "ST1076"
    assert c.visitor_id == "101"
    assert c.zone_id == "PURPLLE_MUM_1076_Z01"
    assert c.zone_hotspot_x == 412.6


def test_canonical_from_queue():
    e = QueueEvent.model_validate_json(QUEUE_RAW)
    c = to_canonical(e)
    assert c.store_id == "ST1076"
    assert c.visitor_id == "102"
    assert c.abandoned is False
    assert c.queue_event_id == "cfd8e3c5-7aa0-4ea3-9b59-692d50da8308"
    assert c.wait_seconds == 8


def test_canonical_from_abandoned():
    e = QueueEvent.model_validate_json(ABAND_RAW)
    c = to_canonical(e)
    assert c.abandoned is True
    assert c.queue_served_ts is None


def test_canonical_raw_preserved():
    e = EntryExitEvent.model_validate_json(ENTRY_RAW)
    c = to_canonical(e)
    assert c.raw["id_token"] == "ID_60001"


# ---------------------------------------------------------------------------
# Guard: no REENTRY event type
# ---------------------------------------------------------------------------

def test_no_reentry_event_type():
    """re-entry is modelled in logic, not as an event_type."""
    with pytest.raises((ValidationError, ValueError)):
        EntryExitEvent.model_validate({
            "event_type": "reentry",
            "id_token": "ID_99999",
            "store_code": "store_1008",
            "camera_id": "CAM_ENTRY_01",
            "event_timestamp": "2026-04-10T12:00:00",
            "is_staff": False,
        })


def test_event_batch_rejects_unknown_type():
    with pytest.raises((ValidationError, ValueError)):
        EventBatch(events=[{"event_type": "reentry", "id_token": "x"}])
