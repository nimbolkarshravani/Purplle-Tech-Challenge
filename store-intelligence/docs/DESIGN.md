# Store Intelligence — Design Document

## Problem statement

Given a stream of structured events from CCTV-based person detection (entry/exit, zone dwell, billing queue), compute real-time store metrics, conversion funnels, zone heatmaps, and anomaly signals — all accessible via a REST API.

## Component overview

### 1. Event ingestion (`app/ingestion.py`)

- Accepts batches of up to 500 events via `POST /events/ingest`
- Three event families, validated via Pydantic discriminated union on `event_type`:
  - **Entry/exit**: `entry`, `exit` — carry `id_token` (face recognition)
  - **Zone**: `zone_entered`, `zone_exited` — carry `track_id` (floor tracking)
  - **Queue**: `queue_completed`, `queue_abandoned` — carry `track_id`
- Idempotency: each event is keyed by SHA-256 of `(store_id, event_type, event_timestamp, id_token|track_id)`. Duplicate inserts are silently ignored via `INSERT OR IGNORE`.
- Returns `200` on full success, `207` on partial (some events rejected), `400` on invalid JSON. Never returns 5xx for malformed payloads.

### 2. Storage (`app/db.py`)

- SQLite with WAL mode — allows concurrent reads during writes
- Single `events` table: all event types co-located, with nullable columns for each family
- Store IDs are normalised: `store_1008` ↔ `ST1008`
- DB path is injectable for test isolation (each test gets a temp DB)

### 3. Metrics (`app/metrics.py`)

**Visitor metrics:**
- `unique_visitors`: deduplicated `id_token` count from `entry` events (excluding `is_staff=true`)
- `conversion_rate`: `queue_completed` track_ids / unique_visitors
- `avg_dwell_per_zone`: paired `zone_entered`/`zone_exited` events by `(track_id, zone_id)`, taking the earliest subsequent exit within a 2-hour sanity cap

**Heatmap:**
- Frequency score: visit count per zone, normalised 0–100 relative to max zone
- Dwell score: average dwell seconds per zone, normalised 0–100 relative to max zone
- `data_confidence`: `"ok"` if ≥ 20 unique sessions, `"low"` otherwise

**Store-agnostic guarantee:** Unknown store IDs return zeroed metrics — no 404, no crash.

### 4. Conversion funnel (`app/funnel.py`)

4-step funnel with monotonic counts (each step ≤ previous):

| Step | Camera type | Identifier |
|------|-------------|------------|
| 1. Entry | Entry camera | `id_token` (deduplicated) |
| 2. Zone visit | Floor camera | `track_id` |
| 3. Billing queue | Billing camera | `track_id` |
| 4. Purchase | Billing camera | `track_id` (queue_completed only) |

**Schema gap**: entry cameras use `id_token`; floor/billing cameras use `track_id`. These are different identifier spaces with no join key in the event schema, so step 1→2 counts are not directly comparable. Drop-off % is computed but should be interpreted as approximate.

### 5. Anomaly detection (`app/anomalies.py`)

Three anomaly types:

**VISITOR_SPIKE**
- Rolling baseline: current-hour entry count vs. 7-day same-hour rolling average
- Triggers when current > `SPIKE_MULTIPLIER` (2.0×) × rolling average
- Cold-start fallback: if store has < 3 days of history (`baseline = "static"`), triggers when current hour count > `SPIKE_STATIC_THRESHOLD` (30)
- Severity: `"warning"`

**QUEUE_ABANDONMENT_SURGE**
- Window: last 30 minutes of queue events
- Triggers when abandoned / (completed + abandoned) > 50%
- Severity: `"critical"`

**DEAD_ZONE**
- Condition: zone has historical visits but 0 visits in last 2 hours
- Guard: only fires when store has ≥ 3 recent entry events (store is active)
- Severity: `"info"`

### 6. Health check (`app/health.py`)

- Reports last event timestamp per store
- Flags `STALE_FEED` when last event > 10 minutes ago

## Non-functional properties

- **Structured JSON logging**: every request logged with `trace_id`, `method`, `path`, `status_code`, `latency_ms`
- **Graceful 503**: all DB-touching endpoints catch exceptions and return `{"status":"error","error":"db_unavailable"}` with HTTP 503
- **No global state**: DB path passed as parameter everywhere — enables parallel test isolation
- **Store-agnostic**: all endpoints accept any `store_id` string; no hardcoded store list

## Configuration constants

```python
VISITOR_TO_BUYER_RATIO = 2.5       # estimated visitors per confirmed purchase
BILLING_CAPTURE_MISS_RATE = 0.07   # 7% of buyers emit no queue_completed

SPIKE_MULTIPLIER = 2.0
SPIKE_STATIC_THRESHOLD = 30
MIN_HISTORY_DAYS = 3
ABANDONMENT_WINDOW_MINUTES = 30
ABANDONMENT_RATE_THRESHOLD = 0.5
DEAD_ZONE_WINDOW_HOURS = 2
```
