# Store Intelligence

A containerised store-intelligence system: CCTV clips -> detection pipeline -> structured event stream -> FastAPI API -> live terminal dashboard.

## Quick start (5 commands)

```bash
git clone https://github.com/nimbolkarshravani/Purplle-Tech-Challenge.git
cd Purplle-Tech-Challenge
pip install -r requirements.txt
python pipeline/generate_events.py        # creates data/events.jsonl
uvicorn app.main:app --reload             # starts the API on :8000
```

To run with Docker:
```bash
docker compose up
```

## Run the detection pipeline against a clip
```bash
python pipeline/detect.py --clip path/to/clip.mp4 --out data/events.jsonl
```

## Run tests
```bash
pytest --cov=app --cov-report=term-missing
```

## Data files
`data/events.jsonl` is generated output and excluded from git. Run `python pipeline/generate_events.py` to create it.
It reads the POS CSV from `inputs/` and synthesises 7 days of prior history plus the real April 10 day.

## assertions.py
No `assertions.py` was found in `inputs/`. The test suite covers all required endpoint behaviours.

## Architecture
See `docs/DESIGN.md`.
