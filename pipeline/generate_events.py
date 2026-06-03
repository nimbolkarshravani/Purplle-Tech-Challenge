"""
generate_events.py

Synthesizes data/events.jsonl for store ST1008 (Brigade Road, Bengaluru).

Design notes:
- VISITOR_TO_BUYER_RATIO: documented config constant. At 2.5, ~40% of unique
  visitors are buyers, giving realistic funnel drop-off.
- JUNK_AMOUNT_THRESHOLD: POS rows with total_amount below this are excluded.
- 7-day synthetic history (April 3-9 2026) is derived from April 10 POS
  patterns with +-HISTORY_VARIANCE daily noise. Code paths are labelled
  `# SYNTHETIC_HISTORY` for auditing.
- Cold-start path: if POS file is absent, writes 3 minimal valid events and
  prints a warning. Triggered with --cold-start flag or missing input file.
- All store ids use the exact schema format: entry/exit -> store_code (store_1008),
  zone/queue -> store_id (ST1008).
"""
import csv
import json
import random
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config constants (documented, never hardcode outputs)
# ---------------------------------------------------------------------------
VISITOR_TO_BUYER_RATIO = 2.5   # total unique visitors per converting visitor
JUNK_AMOUNT_THRESHOLD = 10.0   # POS rows below this are excluded
STAFF_FRACTION = 0.05          # fraction of non-staff sessions that are staff
HISTORY_DAYS = 7               # synthetic prior days before April 10
HISTORY_VARIANCE = 0.20        # daily traffic variance for synthetic history
SEED = 42

STORE_CODE = "store_1008"      # entry/exit field
STORE_ID = "ST1008"            # zone/queue field

BILLING_ZONE_ID = "PURPLLE_BLR_1008_Z_BILLING"
BILLING_CAMERA = "CAM_BILLING_01"
FLOOR_CAMERA = "CAM_FLOOR_01"
ENTRY_CAMERA = "CAM_ENTRY_01"

# ---------------------------------------------------------------------------
# Zone catalog
# ---------------------------------------------------------------------------
SHELF_ZONES = [
    {"zone_id": "PURPLLE_BLR_1008_Z_EB_KOREAN",     "zone_name": "EB Korean",       "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 180, "hotspot_y": 120},
    {"zone_id": "PURPLLE_BLR_1008_Z_THE_FACE_SHOP", "zone_name": "The Face Shop",   "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 220, "hotspot_y": 130},
    {"zone_id": "PURPLLE_BLR_1008_Z_GOOD_VIBES",    "zone_name": "Good Vibes",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 260, "hotspot_y": 120},
    {"zone_id": "PURPLLE_BLR_1008_Z_DERMDOC",       "zone_name": "Dermdoc",         "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 300, "hotspot_y": 130},
    {"zone_id": "PURPLLE_BLR_1008_Z_MINIMALIST",    "zone_name": "Minimalist",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 340, "hotspot_y": 120},
    {"zone_id": "PURPLLE_BLR_1008_Z_AQUALOGICA",    "zone_name": "Aqualogica",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 380, "hotspot_y": 130},
    {"zone_id": "PURPLLE_BLR_1008_Z_LAKME_SKIN",    "zone_name": "Lakme Skin",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 420, "hotspot_y": 120},
    {"zone_id": "PURPLLE_BLR_1008_Z_ACCESSORIES",   "zone_name": "Accessories",     "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 460, "hotspot_y": 130},
    {"zone_id": "PURPLLE_BLR_1008_Z_MAYBELLINE",    "zone_name": "Maybelline",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 180, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_FACES_CANADA",  "zone_name": "Faces Canada",    "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 220, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_LAKME",         "zone_name": "Lakme",           "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 260, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_COLORBAR_SUGAR","zone_name": "Colorbar Sugar",  "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 300, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_SWISS_BEAUTY",  "zone_name": "Swiss Beauty",    "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 340, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_RENEE_NYBAE",   "zone_name": "Renee Nybae",     "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 380, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_ALPS_GOODNESS", "zone_name": "Alps Goodness",   "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 420, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_STREAX",        "zone_name": "Streax",          "zone_type": "SHELF",   "is_revenue_zone": "Yes", "hotspot_x": 460, "hotspot_y": 200},
    {"zone_id": "PURPLLE_BLR_1008_Z_NAIL_FRAGRANCE","zone_name": "Nail Fragrance",  "zone_type": "DISPLAY", "is_revenue_zone": "Yes", "hotspot_x": 500, "hotspot_y": 300},
    {"zone_id": "PURPLLE_BLR_1008_Z_MAKEUP_UNIT",   "zone_name": "Makeup Unit",     "zone_type": "DISPLAY", "is_revenue_zone": "Yes", "hotspot_x": 540, "hotspot_y": 300},
    {"zone_id": "PURPLLE_BLR_1008_Z_PMU",           "zone_name": "PMU Unit",        "zone_type": "DISPLAY", "is_revenue_zone": "Yes", "hotspot_x": 580, "hotspot_y": 300},
]

ZONE_BY_ID = {z["zone_id"]: z for z in SHELF_ZONES}

BRAND_TO_ZONE = {
    "Faces Canada":     "PURPLLE_BLR_1008_Z_FACES_CANADA",
    "Good Vibes":       "PURPLLE_BLR_1008_Z_GOOD_VIBES",
    "DERMDOC":          "PURPLLE_BLR_1008_Z_DERMDOC",
    "Minimalist":       "PURPLLE_BLR_1008_Z_MINIMALIST",
    "Lakme":            "PURPLLE_BLR_1008_Z_LAKME",
    "Maybelline":       "PURPLLE_BLR_1008_Z_MAYBELLINE",
    "Swiss Beauty":     "PURPLLE_BLR_1008_Z_SWISS_BEAUTY",
    "Alps Goodness":    "PURPLLE_BLR_1008_Z_ALPS_GOODNESS",
    "NY Bae":           "PURPLLE_BLR_1008_Z_RENEE_NYBAE",
    "Renee":            "PURPLLE_BLR_1008_Z_RENEE_NYBAE",
    "Beauty of Joseon": "PURPLLE_BLR_1008_Z_EB_KOREAN",
    "COSRX":            "PURPLLE_BLR_1008_Z_EB_KOREAN",
    "Round Lab":        "PURPLLE_BLR_1008_Z_EB_KOREAN",
}

BILLING_ZONE_INFO = {
    "zone_id":   BILLING_ZONE_ID,
    "zone_name": "Billing Counter",
    "zone_type": "BILLING",
    "is_revenue_zone": "Yes",
    "hotspot_x": 600,
    "hotspot_y": 100,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _age_bucket(age: int) -> str:
    if age < 18:
        return "Under 18"
    if age < 25:
        return "18-24"
    if age < 35:
        return "25-34"
    if age < 45:
        return "35-44"
    return "45+"


def _demo():
    gender = random.choice(["F", "F", "F", "M"])  # beauty store skews female
    age = random.randint(18, 45)
    return gender, age


def _entry(id_token, ts, is_staff, gender, age, group_id=None, group_size=None):
    return {
        "event_type":       "entry",
        "id_token":         id_token,
        "store_code":       STORE_CODE,
        "camera_id":        ENTRY_CAMERA,
        "event_timestamp":  _ts(ts),
        "is_staff":         is_staff,
        "gender_pred":      gender,
        "age_pred":         age,
        "age_bucket":       _age_bucket(age),
        "is_face_hidden":   False,
        "group_id":         group_id,
        "group_size":       group_size,
    }


def _exit(id_token, ts, is_staff, gender, age, group_id=None, group_size=None):
    return {
        "event_type":       "exit",
        "id_token":         id_token,
        "store_code":       STORE_CODE,
        "camera_id":        ENTRY_CAMERA,
        "event_timestamp":  _ts(ts),
        "is_staff":         is_staff,
        "gender_pred":      gender,
        "age_pred":         age,
        "age_bucket":       _age_bucket(age),
        "is_face_hidden":   False,
        "group_id":         group_id,
        "group_size":       group_size,
    }


def _zone_entered(track_id, zone, ts, gender, age):
    return {
        "event_type":       "zone_entered",
        "track_id":         track_id,
        "store_id":         STORE_ID,
        "camera_id":        zone.get("camera_id", FLOOR_CAMERA),
        "zone_id":          zone["zone_id"],
        "zone_name":        zone["zone_name"],
        "zone_type":        zone["zone_type"],
        "is_revenue_zone":  zone["is_revenue_zone"],
        "event_time":       _ts(ts),
        "zone_hotspot_x":   round(zone["hotspot_x"] + random.uniform(-5, 5), 1),
        "zone_hotspot_y":   round(zone["hotspot_y"] + random.uniform(-5, 5), 1),
        "gender":           gender,
        "age":              age,
        "age_bucket":       _age_bucket(age),
    }


def _zone_exited(track_id, zone, ts, gender, age):
    return {
        "event_type":       "zone_exited",
        "track_id":         track_id,
        "store_id":         STORE_ID,
        "camera_id":        zone.get("camera_id", FLOOR_CAMERA),
        "zone_id":          zone["zone_id"],
        "zone_name":        zone["zone_name"],
        "zone_type":        zone["zone_type"],
        "is_revenue_zone":  zone["is_revenue_zone"],
        "event_time":       _ts(ts),
        "zone_hotspot_x":   round(zone["hotspot_x"] + random.uniform(-5, 5), 1),
        "zone_hotspot_y":   round(zone["hotspot_y"] + random.uniform(-5, 5), 1),
        "gender":           gender,
        "age":              age,
        "age_bucket":       _age_bucket(age),
    }


def _queue(track_id, join_ts, served_ts, exit_ts, wait_secs, pos, abandoned, gender, age):
    return {
        "queue_event_id":        str(uuid.uuid4()),
        "event_type":            "queue_abandoned" if abandoned else "queue_completed",
        "track_id":              track_id,
        "store_id":              STORE_ID,
        "camera_id":             BILLING_CAMERA,
        "zone_id":               BILLING_ZONE_INFO["zone_id"],
        "zone_name":             BILLING_ZONE_INFO["zone_name"],
        "zone_type":             BILLING_ZONE_INFO["zone_type"],
        "is_revenue_zone":       BILLING_ZONE_INFO["is_revenue_zone"],
        "queue_join_ts":         _ts(join_ts),
        "queue_served_ts":       _ts(served_ts) if served_ts else None,
        "queue_exit_ts":         _ts(exit_ts),
        "wait_seconds":          wait_secs,
        "queue_position_at_join": pos,
        "abandoned":             abandoned,
        "zone_hotspot_x":        round(BILLING_ZONE_INFO["hotspot_x"] + random.uniform(-10, 10), 1),
        "zone_hotspot_y":        round(BILLING_ZONE_INFO["hotspot_y"] + random.uniform(-10, 10), 1),
        "gender":                gender,
        "age":                   age,
        "age_bucket":            _age_bucket(age),
    }


# ---------------------------------------------------------------------------
# Session generator
# ---------------------------------------------------------------------------

def _visitor_session(
    idx: int,
    id_base: int,
    track_base: int,
    entry_ts: datetime,
    is_staff: bool,
    is_converting: bool,
    brands: set,
    transaction_ts: datetime = None,
    group_id: str = None,
    group_size: int = None,
):
    events = []
    id_token = f"ID_{id_base + idx:05d}"
    track_id = track_base + idx
    gender, age = _demo()

    events.append(_entry(id_token, entry_ts, is_staff, gender, age, group_id, group_size))

    if is_staff:
        exit_ts = entry_ts + timedelta(hours=random.uniform(4, 8))
        events.append(_exit(id_token, exit_ts, True, gender, age))
        return events

    cursor = entry_ts + timedelta(minutes=random.uniform(1, 3))

    if is_converting and brands:
        seen = set()
        for brand in brands:
            zid = BRAND_TO_ZONE.get(brand)
            if zid and zid not in seen and zid in ZONE_BY_ID:
                seen.add(zid)
                zone = ZONE_BY_ID[zid]
                dwell = timedelta(seconds=random.randint(45, 180))
                events.append(_zone_entered(track_id, zone, cursor, gender, age))
                cursor += dwell
                events.append(_zone_exited(track_id, zone, cursor, gender, age))
                cursor += timedelta(seconds=random.randint(10, 30))
    else:
        for zone in random.sample(SHELF_ZONES, min(random.randint(1, 3), len(SHELF_ZONES))):
            dwell = timedelta(seconds=random.randint(20, 90))
            events.append(_zone_entered(track_id, zone, cursor, gender, age))
            cursor += dwell
            events.append(_zone_exited(track_id, zone, cursor, gender, age))
            cursor += timedelta(seconds=random.randint(10, 30))

    if is_converting and transaction_ts:
        join_ts = transaction_ts - timedelta(minutes=random.uniform(3, 8))
        join_ts = max(join_ts, cursor + timedelta(seconds=30))
        wait_secs = random.randint(5, 120)
        served_ts = join_ts + timedelta(seconds=wait_secs)
        exit_ts = served_ts + timedelta(minutes=random.uniform(1, 3))
        events.append(_queue(track_id, join_ts, served_ts, exit_ts, wait_secs, random.randint(1, 5), False, gender, age))
        cursor = exit_ts
    elif not is_converting and random.random() < 0.15:
        join_ts = cursor + timedelta(minutes=random.uniform(1, 3))
        wait_secs = random.randint(60, 300)
        exit_ts = join_ts + timedelta(seconds=wait_secs)
        events.append(_queue(track_id, join_ts, None, exit_ts, wait_secs, random.randint(2, 6), True, gender, age))
        cursor = exit_ts

    exit_ts = cursor + timedelta(minutes=random.uniform(1, 5))
    events.append(_exit(id_token, exit_ts, False, gender, age, group_id, group_size))
    return events


# ---------------------------------------------------------------------------
# Day generator
# ---------------------------------------------------------------------------

def _generate_day(date: datetime, baskets: list, id_base: int, track_base: int, synthetic: bool):
    """Generate all events for one store day. Returns (events, next_id_base, next_track_base)."""
    events = []
    idx = 0

    if synthetic:
        # SYNTHETIC_HISTORY: scale April 10 basket count by random daily variance
        # derived from real April 10 patterns; variance capped at +-HISTORY_VARIANCE
        scale = 1.0 + random.uniform(-HISTORY_VARIANCE, HISTORY_VARIANCE)
        n_conv = max(1, round(len(baskets) * scale))
        store_open = date.replace(hour=11, minute=0, second=0, microsecond=0)
        store_close = date.replace(hour=21, minute=30, second=0, microsecond=0)
        span_minutes = int((store_close - store_open).total_seconds() // 60)
        use_baskets = []
        for i in range(n_conv):
            frac = i / n_conv
            tx_ts = store_open + timedelta(minutes=int(frac * span_minutes) + random.randint(0, 15))
            real = baskets[i % len(baskets)]
            use_baskets.append({"time": tx_ts, "brands": real["brands"]})
    else:
        # April 10: anchor to real POS transaction times
        use_baskets = [
            {"time": date.replace(hour=b["time"].hour, minute=b["time"].minute, second=b["time"].second), "brands": b["brands"]}
            for b in baskets
        ]

    n_conv = len(use_baskets)
    n_total = round(n_conv * VISITOR_TO_BUYER_RATIO)
    n_non_conv = n_total - n_conv

    # converting sessions
    for basket in use_baskets:
        entry_ts = basket["time"] - timedelta(minutes=random.uniform(10, 25))
        events.extend(_visitor_session(idx, id_base, track_base, entry_ts, False, True, basket["brands"], basket["time"]))
        idx += 1

    # non-converting sessions spread across store hours
    store_open = date.replace(hour=11, minute=0, second=0, microsecond=0)
    store_span = int((date.replace(hour=21, minute=30) - store_open).total_seconds() // 60)
    for _ in range(n_non_conv):
        entry_ts = store_open + timedelta(minutes=random.randint(0, store_span))
        events.extend(_visitor_session(idx, id_base, track_base, entry_ts, False, False, set()))
        idx += 1

    # staff entries
    n_staff = max(1, round(n_conv * STAFF_FRACTION))
    for i in range(n_staff):
        entry_ts = date.replace(hour=10, minute=random.randint(0, 30), second=0, microsecond=0)
        events.extend(_visitor_session(idx, id_base, track_base, entry_ts, True, False, set()))
        idx += 1

    return events, id_base + idx, track_base + idx


# ---------------------------------------------------------------------------
# POS loader
# ---------------------------------------------------------------------------

def _load_pos(repo_root: Path) -> list:
    """Find and parse the POS CSV. Returns list of basket dicts with time + brands."""
    candidates = list(repo_root.glob("inputs/Brigade_Bangalore*.csv")) + list(repo_root.glob("Brigade_Bangalore*.csv"))
    if not candidates:
        return []
    pos_file = candidates[0]
    print(f"Loading POS from: {pos_file.name}")
    baskets = {}
    with open(pos_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                amt = float(row["total_amount"])
            except (ValueError, KeyError):
                continue
            if amt < JUNK_AMOUNT_THRESHOLD:
                continue
            oid = row["order_id"]
            dt = datetime.strptime(f"{row['order_date']} {row['order_time']}", "%d-%m-%Y %H:%M:%S")
            if oid not in baskets:
                baskets[oid] = {"time": dt, "brands": set()}
            baskets[oid]["brands"].add(row.get("brand_name", ""))
    return list(baskets.values())


# ---------------------------------------------------------------------------
# Cold-start path
# ---------------------------------------------------------------------------

def _cold_start_events() -> list:
    """Minimal valid event set emitted when POS data is unavailable."""
    ts = datetime(2026, 4, 10, 12, 0, 0)
    events = []
    for i in range(3):
        g, a = _demo()
        t = ts + timedelta(minutes=i * 5)
        events.append(_entry(f"ID_{90000 + i:05d}", t, False, g, a))
        events.append(_exit(f"ID_{90000 + i:05d}", t + timedelta(minutes=15), False, g, a))
    return events


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _summary(events: list):
    types = Counter(e["event_type"] for e in events)
    entries = [e for e in events if e["event_type"] == "entry"]
    all_visitors = {e["id_token"] for e in entries if not e["is_staff"]}
    staff_ids = {e["id_token"] for e in entries if e["is_staff"]}

    print("\n=== Event Summary ===")
    print(f"Total events : {len(events)}")
    print(f"Event types  : {dict(types)}")
    print(f"Unique visitors (non-staff) : {len(all_visitors)}")
    print(f"Staff sessions             : {len(staff_ids)}")
    print(f"queue_completed            : {types['queue_completed']}")
    print(f"queue_abandoned            : {types['queue_abandoned']}")

    for day_label in ["2026-04-10"]:
        day_entries = [e for e in entries if e["event_timestamp"].startswith(day_label) and not e["is_staff"]]
        day_v = {e["id_token"] for e in day_entries}
        day_q = sum(1 for e in events if e["event_type"] == "queue_completed" and (e.get("queue_join_ts") or "").startswith(day_label))
        rate = (day_q / len(day_v) * 100) if day_v else 0.0
        print(f"\n{day_label} unique visitors  : {len(day_v)}")
        print(f"{day_label} converting       : {day_q}")
        print(f"{day_label} conversion rate  : {rate:.1f}%  (VISITOR_TO_BUYER_RATIO={VISITOR_TO_BUYER_RATIO})")

    def _evt_ts(e):
        return e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts") or ""

    print("\n=== First 15 events (all families) ===")
    for e in sorted(events, key=_evt_ts)[:15]:
        ts = _evt_ts(e)
        sid = e.get("store_code") or e.get("store_id", "")
        vid = e.get("id_token") or e.get("track_id", "")
        print(f"  {e['event_type']:22s} | {ts} | {sid:12s} | {vid}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(cold_start: bool = False):
    random.seed(SEED)
    repo_root = Path(__file__).resolve().parent.parent
    out_path = repo_root / "data" / "events.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    baskets = [] if cold_start else _load_pos(repo_root)

    if not baskets:
        print("COLD START: no valid POS data found. Writing minimal valid event set.")
        events = _cold_start_events()
        _write(out_path, events)
        _summary(events)
        return

    print(f"Valid POS baskets (after junk filter): {len(baskets)}")

    all_events = []
    id_base, track_base = 10000, 5000
    april_10 = datetime(2026, 4, 10)

    # SYNTHETIC_HISTORY: 7 prior days derived from April 10 with variance
    for offset in range(HISTORY_DAYS, 0, -1):
        hist_date = april_10 - timedelta(days=offset)
        day_evts, id_base, track_base = _generate_day(hist_date, baskets, id_base, track_base, synthetic=True)
        all_events.extend(day_evts)
        print(f"  synthetic {hist_date.date()}: {len(day_evts):5d} events")

    # April 10: anchored to real POS times
    day_evts, id_base, track_base = _generate_day(april_10, baskets, id_base, track_base, synthetic=False)
    all_events.extend(day_evts)
    print(f"  real      {april_10.date()}: {len(day_evts):5d} events")

    _write(out_path, all_events)
    _summary(all_events)


def _write(path: Path, events: list):
    def _sort_key(e):
        return e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts") or ""
    events_sorted = sorted(events, key=_sort_key)
    with open(path, "w", encoding="utf-8") as f:
        for e in events_sorted:
            f.write(json.dumps(e) + "\n")
    print(f"\nWrote {len(events_sorted)} events -> {path}")


if __name__ == "__main__":
    main(cold_start="--cold-start" in sys.argv)
