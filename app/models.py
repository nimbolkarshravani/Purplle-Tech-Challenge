"""Pydantic models for all three event families, canonical normalizer, and EventBatch."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


def normalize_store_id(code: str) -> str:
    """Return canonical ST-prefixed store id. store_1008 -> ST1008, ST1008 -> ST1008."""
    code = code.strip()
    m = re.match(r"store_?(\d+)", code, re.IGNORECASE)
    if m:
        return f"ST{m.group(1)}"
    if re.match(r"ST\d+", code, re.IGNORECASE):
        return code.upper()
    return code.upper()


def store_id_to_code(store_id: str) -> str:
    """Return lowercase store_NNNN code. ST1008 -> store_1008."""
    store_id = store_id.strip()
    m = re.match(r"ST(\d+)", store_id, re.IGNORECASE)
    if m:
        return f"store_{m.group(1)}"
    return store_id.lower()


class EntryExitEvent(BaseModel):
    event_type: Literal["entry", "exit"]
    id_token: str
    store_code: str
    camera_id: str
    event_timestamp: datetime
    is_staff: bool
    gender_pred: Optional[str] = None
    age_pred: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: bool = False
    group_id: Optional[str] = None
    group_size: Optional[int] = None


class ZoneEvent(BaseModel):
    event_type: Literal["zone_entered", "zone_exited"]
    track_id: int
    store_id: str
    camera_id: str
    zone_id: str
    zone_name: str
    zone_type: str
    is_revenue_zone: str
    event_time: datetime
    zone_hotspot_x: float
    zone_hotspot_y: float
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None


class QueueEvent(BaseModel):
    queue_event_id: str
    event_type: Literal["queue_completed", "queue_abandoned"]
    track_id: int
    store_id: str
    camera_id: str
    zone_id: str
    zone_name: str
    zone_type: str
    is_revenue_zone: str
    queue_join_ts: datetime
    queue_served_ts: Optional[datetime] = None
    queue_exit_ts: datetime
    wait_seconds: int
    queue_position_at_join: int
    abandoned: bool
    zone_hotspot_x: float
    zone_hotspot_y: float
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None


RawEvent = Annotated[
    Union[EntryExitEvent, ZoneEvent, QueueEvent],
    Field(discriminator="event_type"),
]


class EventBatch(BaseModel):
    events: list[RawEvent]


class CanonicalEvent(BaseModel):
    """Store-agnostic internal representation with unified field names."""
    event_type: str
    store_id: str
    visitor_id: str
    camera_id: str
    timestamp: datetime
    is_staff: bool = False
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: bool = False
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    zone_id: Optional[str] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[str] = None
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    queue_event_id: Optional[str] = None
    queue_join_ts: Optional[datetime] = None
    queue_served_ts: Optional[datetime] = None
    queue_exit_ts: Optional[datetime] = None
    wait_seconds: Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned: Optional[bool] = None
    raw: dict = Field(default_factory=dict)


def to_canonical(event: RawEvent) -> CanonicalEvent:  # type: ignore[valid-type]
    raw = event.model_dump(mode="json")
    if isinstance(event, EntryExitEvent):
        return CanonicalEvent(
            event_type=event.event_type,
            store_id=normalize_store_id(event.store_code),
            visitor_id=event.id_token,
            camera_id=event.camera_id,
            timestamp=event.event_timestamp,
            is_staff=event.is_staff,
            gender=event.gender_pred,
            age=event.age_pred,
            age_bucket=event.age_bucket,
            is_face_hidden=event.is_face_hidden,
            group_id=event.group_id,
            group_size=event.group_size,
            raw=raw,
        )
    if isinstance(event, ZoneEvent):
        return CanonicalEvent(
            event_type=event.event_type,
            store_id=normalize_store_id(event.store_id),
            visitor_id=str(event.track_id),
            camera_id=event.camera_id,
            timestamp=event.event_time,
            zone_id=event.zone_id,
            zone_name=event.zone_name,
            zone_type=event.zone_type,
            is_revenue_zone=event.is_revenue_zone,
            zone_hotspot_x=event.zone_hotspot_x,
            zone_hotspot_y=event.zone_hotspot_y,
            gender=event.gender,
            age=event.age,
            age_bucket=event.age_bucket,
            raw=raw,
        )
    return CanonicalEvent(
        event_type=event.event_type,
        store_id=normalize_store_id(event.store_id),
        visitor_id=str(event.track_id),
        camera_id=event.camera_id,
        timestamp=event.queue_join_ts,
        zone_id=event.zone_id,
        zone_name=event.zone_name,
        zone_type=event.zone_type,
        is_revenue_zone=event.is_revenue_zone,
        zone_hotspot_x=event.zone_hotspot_x,
        zone_hotspot_y=event.zone_hotspot_y,
        queue_event_id=event.queue_event_id,
        queue_join_ts=event.queue_join_ts,
        queue_served_ts=event.queue_served_ts,
        queue_exit_ts=event.queue_exit_ts,
        wait_seconds=event.wait_seconds,
        queue_position_at_join=event.queue_position_at_join,
        abandoned=event.abandoned,
        gender=event.gender,
        age=event.age,
        age_bucket=event.age_bucket,
        raw=raw,
    )
