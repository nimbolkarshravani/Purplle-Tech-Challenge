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
cd Purplle-Tech-Challenge/store-intelligence
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

## Detection pipeline

Run YOLOv8n against a real CCTV clip:

```bash
python pipeline/detect.py --clip path/to/clip.mp4 --out data/events.jsonl
```

Generates structured events conforming to the schema in `data/sample_events.jsonl`.

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
│   ├── detect.py        # YOLOv8n detection → event stream
│   └── generate_events.py  # Synthetic event generator
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
