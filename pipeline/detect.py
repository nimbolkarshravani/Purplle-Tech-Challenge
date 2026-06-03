"""detect.py — CCTV clip → structured event stream using YOLOv8n + ByteTrack.

Usage:
    python pipeline/detect.py --clip clips/entry_cam.mp4 --cam-id CAM_ENTRY_01 \\
        --cam-type entry --store-code store_1008 --out data/events.jsonl

    python pipeline/detect.py --clip clips/floor_cam.mp4 --cam-id CAM_FLOOR_01 \\
        --cam-type zone --store-id ST1008 --zone-id PURPLLE_BLR_1008_Z_SHELF \\
        --zone-name "Shelf A" --out data/events.jsonl

    # Process multiple clips and merge into one file
    python pipeline/detect.py \\
        --clip clips/entry.mp4 --cam-id CAM_ENTRY_01 --cam-type entry \\
        --clip clips/floor.mp4 --cam-id CAM_FLOOR_01 --cam-type zone \\
        --store-code store_1008 --out data/events.jsonl

Design:
  - Person detection: YOLOv8n, class 0 (person), conf >= 0.25
  - Tracking: ByteTrack (built into ultralytics), track_id per session
  - Entry/exit: virtual line at mid-frame on entry cam; crossing direction = entry or exit
  - Staff heuristic: track present for > STAFF_DWELL_SECONDS with no line cross
  - Groups: bounding-box IoU clustering of simultaneous detections (count individuals)
  - Low-confidence detections: emitted with low_confidence=True flag, not dropped
  - id_token: stable hash of (cam_id, track_id, clip_start_ts) for entry/exit events
  - All events conform to the schema in inputs/sample_eventsbe42122.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERSON_CLASS = 0
CONF_THRESHOLD = 0.25          # detections below this are flagged low_confidence
CONF_LOW_FLAG = 0.40           # below this → low_confidence=True in event
IOU_GROUP_THRESHOLD = 0.30     # boxes with this IoU overlap = same group
STAFF_DWELL_SECONDS = 600      # track seen > 10 min with no exit = staff heuristic
LINE_CROSS_MARGIN = 0.05       # fraction of frame height hysteresis around line
SKIP_FRAMES = 2                # process every Nth frame (speed vs accuracy)

STORE_CODE = "store_1008"
STORE_ID = "ST1008"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _age_bucket(age: int) -> str:
    if age < 18: return "Under 18"
    if age < 25: return "18-24"
    if age < 35: return "25-34"
    if age < 45: return "35-44"
    return "45+"


def _id_token(cam_id: str, track_id: int, clip_start: datetime) -> str:
    """Stable per-session visitor ID derived from camera + track + clip start."""
    raw = f"{cam_id}|{track_id}|{clip_start.isoformat()}"
    return "ID_" + hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


def _iou(a, b) -> float:
    """Intersection-over-union of two boxes [x1,y1,x2,y2]."""
    ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2]-a[0]) * (a[3]-a[1])
    area_b = (b[2]-b[0]) * (b[3]-b[1])
    return inter / (area_a + area_b - inter)


def _group_detections(boxes: list) -> list[list[int]]:
    """
    Cluster box indices by pairwise IoU >= IOU_GROUP_THRESHOLD.
    Returns list of groups, each a list of box indices.
    """
    n = len(boxes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i+1, n):
            if _iou(boxes[i], boxes[j]) >= IOU_GROUP_THRESHOLD:
                union(i, j)

    clusters: dict = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    return list(clusters.values())


# ---------------------------------------------------------------------------
# Entry/exit line crossing logic
# ---------------------------------------------------------------------------

class LineCrossTracker:
    """
    Tracks whether each person track has crossed the virtual entry line.

    The entry line is horizontal at y = frame_height * LINE_Y_FRAC (default 0.5).
    - Person moving top→bottom (y increasing) = entry
    - Person moving bottom→top (y decreasing) = exit
    """

    def __init__(self, frame_height: int, line_y_frac: float = 0.5):
        self.line_y = frame_height * line_y_frac
        self.margin = frame_height * LINE_CROSS_MARGIN
        self._zones: dict[int, Optional[str]] = {}
        self._emitted: dict[int, set] = defaultdict(set)

    def _zone(self, cy: float) -> str:
        if cy < self.line_y - self.margin:
            return "above"
        if cy > self.line_y + self.margin:
            return "below"
        return "line"

    def update(self, track_id: int, cy: float) -> Optional[str]:
        """
        Returns "entry", "exit", or None.
        Entry = above→below; Exit = below→above.
        Each direction is emitted at most once per track.
        """
        zone = self._zone(cy)
        prev = self._zones.get(track_id)
        self._zones[track_id] = zone if zone != "line" else prev

        if zone == "line" or prev is None or prev == zone:
            return None

        if prev == "above" and zone == "below" and "entry" not in self._emitted[track_id]:
            self._emitted[track_id].add("entry")
            return "entry"
        if prev == "below" and zone == "above" and "exit" not in self._emitted[track_id]:
            self._emitted[track_id].add("exit")
            return "exit"
        return None


# ---------------------------------------------------------------------------
# Per-track session state
# ---------------------------------------------------------------------------

class TrackState:
    def __init__(self, track_id: int, first_seen: datetime, box, conf: float):
        self.track_id = track_id
        self.first_seen = first_seen
        self.last_seen = first_seen
        self.first_box = box
        self.last_box = box
        self.max_conf = conf
        self.min_conf = conf
        self.crossed: Optional[str] = None
        self.is_staff = False
        self.group_id: Optional[str] = None
        self.group_size: Optional[int] = None

    def update(self, ts: datetime, box, conf: float):
        self.last_seen = ts
        self.last_box = box
        self.max_conf = max(self.max_conf, conf)
        self.min_conf = min(self.min_conf, conf)

    @property
    def dwell_seconds(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds()

    @property
    def low_confidence(self) -> bool:
        return self.max_conf < CONF_LOW_FLAG


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _build_entry_exit(
    event_type: str,
    state: TrackState,
    ts: datetime,
    cam_id: str,
    store_code: str,
    clip_start: datetime,
) -> dict:
    id_token = _id_token(cam_id, state.track_id, clip_start)
    box = state.last_box
    return {
        "event_type": event_type,
        "id_token": id_token,
        "store_code": store_code,
        "camera_id": cam_id,
        "event_timestamp": _ts(ts),
        "is_staff": state.is_staff,
        "gender_pred": None,
        "age_pred": None,
        "age_bucket": None,
        "is_face_hidden": False,
        "group_id": state.group_id,
        "group_size": state.group_size,
        "_track_id": state.track_id,
        "_conf": round(state.max_conf, 3),
        "_low_confidence": state.low_confidence,
        "_bbox": [round(v) for v in box],
    }


def _build_zone_event(
    event_type: str,
    state: TrackState,
    ts: datetime,
    cam_id: str,
    store_id: str,
    zone_id: str,
    zone_name: str,
    zone_type: str = "SHELF",
    is_revenue_zone: str = "Yes",
) -> dict:
    box = state.last_box
    cx = round((box[0] + box[2]) / 2, 1)
    cy = round((box[1] + box[3]) / 2, 1)
    return {
        "event_type": event_type,
        "track_id": state.track_id,
        "store_id": store_id,
        "camera_id": cam_id,
        "zone_id": zone_id,
        "zone_name": zone_name,
        "zone_type": zone_type,
        "is_revenue_zone": is_revenue_zone,
        "event_time": _ts(ts),
        "zone_hotspot_x": cx,
        "zone_hotspot_y": cy,
        "gender": None,
        "age": None,
        "age_bucket": None,
        "_track_id": state.track_id,
        "_conf": round(state.max_conf, 3),
        "_low_confidence": state.low_confidence,
    }


# ---------------------------------------------------------------------------
# Main per-clip processor
# ---------------------------------------------------------------------------

def process_clip(
    clip_path: Path,
    cam_id: str,
    cam_type: str,
    store_code: str,
    store_id: str,
    zone_id: str = "",
    zone_name: str = "",
    zone_type: str = "SHELF",
    is_revenue_zone: str = "Yes",
    line_y_frac: float = 0.5,
    model=None,
) -> list[dict]:
    import cv2
    from ultralytics import YOLO

    if model is None:
        model = YOLO("yolov8n.pt")

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        print(f"  ERROR: cannot open {clip_path}", file=sys.stderr)
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    try:
        mtime = clip_path.stat().st_mtime
        clip_start = datetime.utcfromtimestamp(mtime)
    except Exception:
        clip_start = datetime.utcnow()

    print(f"  {clip_path.name}: {frame_w}x{frame_h} @ {fps:.1f}fps, {total_frames} frames")

    line_tracker = LineCrossTracker(frame_h, line_y_frac) if cam_type == "entry" else None
    tracks: dict[int, TrackState] = {}
    zone_presence: dict[int, datetime] = {}
    events: list[dict] = []

    frame_idx = 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % (SKIP_FRAMES + 1) != 0:
            continue

        ts = clip_start + timedelta(seconds=frame_idx / fps)

        results = model.track(
            frame,
            classes=[PERSON_CLASS],
            conf=CONF_THRESHOLD,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )

        if not results or results[0].boxes is None:
            continue

        boxes_data = results[0].boxes
        if boxes_data.id is None:
            continue

        ids = boxes_data.id.cpu().numpy().astype(int)
        xyxys = boxes_data.xyxy.cpu().numpy()
        confs = boxes_data.conf.cpu().numpy()

        box_list = [xyxys[i].tolist() for i in range(len(ids))]
        groups = _group_detections(box_list) if len(box_list) > 1 else [[i] for i in range(len(box_list))]

        idx_to_group: dict[int, tuple] = {}
        for g in groups:
            if len(g) > 1:
                gid = "G_" + str(min(ids[i] for i in g))
                for i in g:
                    idx_to_group[i] = (gid, len(g))

        for idx, (track_id, box, conf) in enumerate(zip(ids, xyxys, confs)):
            if track_id not in tracks:
                tracks[track_id] = TrackState(track_id, ts, box.tolist(), float(conf))
            else:
                tracks[track_id].update(ts, box.tolist(), float(conf))

            state = tracks[track_id]

            if idx in idx_to_group:
                state.group_id, state.group_size = idx_to_group[idx]
            else:
                state.group_id = None
                state.group_size = None

            if state.dwell_seconds > STAFF_DWELL_SECONDS:
                state.is_staff = True

            cy = (box[1] + box[3]) / 2

            if cam_type == "entry" and line_tracker is not None:
                cross = line_tracker.update(track_id, cy)
                if cross and state.crossed != cross:
                    state.crossed = cross
                    events.append(_build_entry_exit(
                        cross, state, ts, cam_id, store_code, clip_start
                    ))

            elif cam_type == "zone":
                if track_id not in zone_presence:
                    zone_presence[track_id] = ts
                    events.append(_build_zone_event(
                        "zone_entered", state, ts, cam_id, store_id,
                        zone_id, zone_name, zone_type, is_revenue_zone,
                    ))

        if cam_type == "zone":
            active_ids = set(ids.tolist())
            for tid in list(zone_presence):
                if tid not in active_ids:
                    state = tracks.get(tid)
                    if state:
                        events.append(_build_zone_event(
                            "zone_exited", state, ts, cam_id, store_id,
                            zone_id, zone_name, zone_type, is_revenue_zone,
                        ))
                    del zone_presence[tid]

    if cam_type == "zone":
        end_ts = clip_start + timedelta(seconds=total_frames / fps)
        for tid in zone_presence:
            state = tracks.get(tid)
            if state:
                events.append(_build_zone_event(
                    "zone_exited", state, end_ts, cam_id, store_id,
                    zone_id, zone_name, zone_type, is_revenue_zone,
                ))

    cap.release()
    events.sort(key=lambda e: e.get("event_timestamp") or e.get("event_time") or "")

    entries  = sum(1 for e in events if e["event_type"] == "entry")
    exits    = sum(1 for e in events if e["event_type"] == "exit")
    zones_e  = sum(1 for e in events if e["event_type"] == "zone_entered")
    zones_x  = sum(1 for e in events if e["event_type"] == "zone_exited")
    low_conf = sum(1 for e in events if e.get("_low_confidence"))
    staff    = sum(1 for e in events if e.get("is_staff"))
    grps     = sum(1 for e in events if e.get("group_id"))

    print(f"    entry={entries}  exit={exits}  zone_entered={zones_e}  zone_exited={zones_x}")
    print(f"    low_confidence={low_conf}  staff={staff}  in_group={grps}  unique_tracks={len(tracks)}")

    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(
        description="YOLOv8n CCTV → event stream",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--clip", action="append", dest="clips", metavar="PATH")
    p.add_argument("--clips-dir", metavar="DIR")
    p.add_argument("--cam-id", action="append", dest="cam_ids")
    p.add_argument("--cam-type", action="append", dest="cam_types",
                   choices=["entry", "zone", "billing"])
    p.add_argument("--store-code", default=STORE_CODE)
    p.add_argument("--store-id", default=STORE_ID)
    p.add_argument("--zone-id", default="PURPLLE_BLR_1008_Z_SHELF")
    p.add_argument("--zone-name", default="Shelf")
    p.add_argument("--zone-type", default="SHELF")
    p.add_argument("--is-revenue-zone", default="Yes")
    p.add_argument("--line-y", type=float, default=0.5)
    p.add_argument("--out", default="data/events.jsonl")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--model", default="yolov8n.pt")
    return p.parse_args()


def main():
    args = _parse_args()

    from ultralytics import YOLO
    print(f"Loading model: {args.model}")
    model = YOLO(args.model)

    clip_specs: list[tuple[Path, str, str]] = []

    if args.clips_dir:
        d = Path(args.clips_dir)
        for ext in ("*.mp4", "*.avi", "*.mov", "*.mkv"):
            for f in sorted(d.glob(ext)):
                name = f.stem.lower()
                if "entry" in name or "entrance" in name:
                    ctype = "entry"
                elif "bill" in name or "checkout" in name or "queue" in name:
                    ctype = "billing"
                else:
                    ctype = "zone"
                cam_id = f"CAM_{f.stem.upper()}"
                clip_specs.append((f, cam_id, ctype))

    if args.clips:
        cam_ids = args.cam_ids or []
        cam_types = args.cam_types or []
        for i, clip in enumerate(args.clips):
            cam_id = cam_ids[i] if i < len(cam_ids) else f"CAM_{i+1:02d}"
            ctype = cam_types[i] if i < len(cam_types) else "zone"
            clip_specs.append((Path(clip), cam_id, ctype))

    if not clip_specs:
        print("No clips specified. Use --clip or --clips-dir.", file=sys.stderr)
        sys.exit(1)

    all_events: list[dict] = []
    for clip_path, cam_id, cam_type in clip_specs:
        print(f"\nProcessing [{cam_type}] {clip_path} → cam={cam_id}")
        evts = process_clip(
            clip_path=clip_path, cam_id=cam_id, cam_type=cam_type,
            store_code=args.store_code, store_id=args.store_id,
            zone_id=args.zone_id, zone_name=args.zone_name,
            zone_type=args.zone_type, is_revenue_zone=args.is_revenue_zone,
            line_y_frac=args.line_y, model=model,
        )
        all_events.extend(evts)

    all_events.sort(key=lambda e: e.get("event_timestamp") or e.get("event_time") or "")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if args.overwrite else "a"
    with open(out, mode, encoding="utf-8") as f:
        for e in all_events:
            f.write(json.dumps(e) + "\n")

    print(f"\nTotal events written: {len(all_events)} → {out}")

    print("\n=== Event summary by camera ===")
    by_cam: dict = defaultdict(lambda: defaultdict(int))
    for e in all_events:
        cam = e.get("camera_id", "unknown")
        by_cam[cam][e["event_type"]] += 1
    for cam, counts in sorted(by_cam.items()):
        print(f"  {cam}:")
        for etype, n in sorted(counts.items()):
            print(f"    {etype}: {n}")

    print("\n=== First 5 events (sample) ===")
    for e in all_events[:5]:
        ts = e.get("event_timestamp") or e.get("event_time") or ""
        vid = e.get("id_token") or e.get("track_id") or ""
        print(f"  {e['event_type']:20s} | {ts} | {e.get('camera_id','')} | {vid}")


if __name__ == "__main__":
    main()
