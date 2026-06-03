# Store Intelligence — Purplle Tech Challenge 2026 Round 2

A containerised store-intelligence system: CCTV clips → detection pipeline → structured event stream → FastAPI API → live terminal dashboard.

## Architecture

```
CCTV clip  →  pipeline/detect.py (YOLOv8n)  →  data/events.jsonl
                                                        │
                                              POST /events/ingest
                                                        │
                                                  SQLite (WAL)
                                                        │
              ┌─────────────────────────────────────────┤
              │             FastAPI endpoints            │
              │  /stores/{id}/metrics                   │
              │  /stores/{id}/funnel                    │
              │  /stores/{id}/heatmap                   │
              │  /stores/{id}/anomalies                 │
              │  /health                                │
              └─────────────────────────────────────────┘
                                                        │
                                         rich terminal dashboard
```

See `docs/DESIGN.md` for full design rationale and `docs/CHOICES.md` for trade-off decisions.

## Quick start

```bash
git clone https://github.com/nimbolkarshravani/Purplle-Tech-Challenge.git
cd Purplle-Tech-Challenge
pip install -r requirements.txt
python pipeline/generate_events.py        # seeds 8 days of synthetic events
uvicorn app.main:app --reload             # API on http://localhost:8000
```

## Docker

```bash
docker compose up --build
```

The API will be available at `http://localhost:8000`. Data is persisted in `./data/` via a bind mount.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/events/ingest` | Ingest batch of up to 500 events (idempotent) |
| GET | `/stores/{store_id}/metrics` | Visitors, conversion rate, dwell, queue stats |
| GET | `/stores/{store_id}/funnel` | 4-step conversion funnel with drop-off % |
| GET | `/stores/{store_id}/heatmap` | Zone frequency + dwell heatmap (normalised 0–100) |
| GET | `/stores/{store_id}/anomalies` | VISITOR_SPIKE, QUEUE_ABANDONMENT_SURGE, DEAD_ZONE |
| GET | `/health` | Per-store last-event time + STALE_FEED warning |

All endpoints return valid JSON for any store ID — unknown stores return zeroed/empty metrics (no 404).

### Example

```bash
# Ingest events
curl -s -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @data/events.jsonl | jq .

# Query metrics for store ST1008
curl -s http://localhost:8000/stores/ST1008/metrics | jq .

# Check anomalies
curl -s http://localhost:8000/stores/ST1008/anomalies | jq .
```

## Detection pipeline (C5)

Clips are **not committed to git** (license). Place them in `clips/` (gitignored).

### Single clip — entry camera

```bash
python pipeline/detect.py \
  --clip clips/entry_cam.mp4 \
  --cam-id CAM_ENTRY_01 \
  --cam-type entry \
  --store-code store_1008 \
  --out data/events.jsonl --overwrite
```

### Single clip — floor/zone camera

```bash
python pipeline/detect.py \
  --clip clips/floor_cam.mp4 \
  --cam-id CAM_FLOOR_01 \
  --cam-type zone \
  --store-id ST1008 \
  --zone-id PURPLLE_BLR_1008_Z_SHELF \
  --zone-name "Shelf A" \
  --out data/events.jsonl
```

### Multiple clips in one pass

```bash
python pipeline/detect.py \
  --clip clips/entry.mp4    --cam-id CAM_ENTRY_01 --cam-type entry \
  --clip clips/floor_a.mp4  --cam-id CAM_FLOOR_01 --cam-type zone \
  --clip clips/floor_b.mp4  --cam-id CAM_FLOOR_02 --cam-type zone \
  --store-code store_1008 --out data/events.jsonl --overwrite
```

### Auto-scan a clips directory

```bash
python pipeline/detect.py --clips-dir clips/ --store-code store_1008 \
  --out data/events.jsonl --overwrite
# Filename heuristic: *entry* → cam-type=entry, *bill*/*queue* → billing, else zone
```

### Key flags

| Flag | Default | Meaning |
|------|---------|--------|
| `--cam-type` | `zone` | `entry`, `zone`, or `billing` |
| `--line-y` | `0.5` | Entry line Y as fraction of frame height |
| `--model` | `yolov8n.pt` | YOLO weights (auto-downloaded on first run) |
| `--overwrite` | append | Overwrite output file instead of appending |

### Detection design

- **Model**: YOLOv8n, person class only (class 0), confidence ≥ 0.25
- **Tracking**: ByteTrack (built into ultralytics) — stable `track_id` per session
- **Entry/exit**: virtual horizontal line at `--line-y` fraction of frame; top→bottom = `entry`, bottom→top = `exit`; each direction emitted once per track
- **Staff heuristic**: track present > 10 min with no line cross → `is_staff=True`
- **Groups**: IoU-based box clustering; individuals counted separately, all share a `group_id`
- **Low-confidence**: detections with conf < 0.40 flagged `_low_confidence=True`, not dropped
- **id_token**: stable SHA-256 hash of `(cam_id, track_id, clip_start_ts)` for entry/exit events

## Tests

```bash
pytest --cov=app --cov-report=term-missing
```

Current coverage: **91%** across 78 tests.

| Module | Coverage |
|--------|----------|
| anomalies.py | 91% |
| funnel.py | 100% |
| metrics.py | 99% |
| ingestion.py | 90% |
| db.py | 96% |
| models.py | 98% |

## Key design constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `VISITOR_TO_BUYER_RATIO` | 2.5 | Estimated visitors per purchase |
| `BILLING_CAPTURE_MISS_RATE` | 0.07 | 7% of buyers emit no `queue_completed` |
| `SPIKE_MULTIPLIER` | 2.0 | Visitor count > 2× rolling avg triggers spike |
| `SPIKE_STATIC_THRESHOLD` | 30 | Cold-start threshold when < 3 days history |
| `ABANDONMENT_RATE_THRESHOLD` | 0.5 | >50% abandonment rate in last 30 min = surge |
| `DEAD_ZONE_WINDOW_HOURS` | 2 | Zone with 0 visits in last 2h = dead zone |

## Project structure

```
store-intelligence/
├── app/
│   ├── main.py          # FastAPI app, middleware, endpoints
│   ├── db.py            # SQLite init, WAL mode, helper queries
│   ├── models.py        # Pydantic discriminated-union event models
│   ├── ingestion.py     # Batch ingest with SHA-256 idempotency
│   ├── metrics.py       # Visitor metrics + zone heatmap
│   ├── funnel.py        # 4-step conversion funnel
│   ├── anomalies.py     # Anomaly detection (3 types)
│   └── health.py        # Feed staleness check
├── pipeline/
│   ├── detect.py        # YOLOv8n + ByteTrack → event stream
│   └── generate_events.py  # Synthetic event generator
├── clips/               # CCTV clips (gitignored, not redistributed)
├── tests/               # 78 pytest tests (91% coverage)
├── data/                # events.jsonl (git-ignored)
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pytest.ini
```
