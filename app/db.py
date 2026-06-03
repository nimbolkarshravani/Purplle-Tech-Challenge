"""SQLite persistence layer. All queries go through here."""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", "data/store_intelligence.db"))


def init_db(path: Path = None) -> None:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key   TEXT    UNIQUE NOT NULL,
                store_id    TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,
                visitor_id  TEXT,
                timestamp   TEXT,
                raw_json    TEXT    NOT NULL,
                ingested_at TEXT    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_store_ts "
            "ON events(store_id, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_type "
            "ON events(store_id, event_type)"
        )
        conn.commit()


@contextmanager
def get_db(path: Path = None):
    p = path or DB_PATH
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def make_event_key(canonical) -> str:
    """Stable deterministic idempotency key for a canonical event."""
    if canonical.queue_event_id:
        return f"queue|{canonical.queue_event_id}"
    parts = "|".join([
        canonical.store_id,
        canonical.event_type,
        canonical.visitor_id or "",
        canonical.timestamp.isoformat() if canonical.timestamp else "",
        canonical.zone_id or "",
    ])
    return hashlib.sha256(parts.encode()).hexdigest()


def insert_event(conn: sqlite3.Connection, canonical, raw: dict) -> bool:
    """Insert event. Returns True if new, False if duplicate (idempotent)."""
    key = make_event_key(canonical)
    ts = canonical.timestamp.isoformat() if canonical.timestamp else None
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO events
            (event_key, store_id, event_type, visitor_id, timestamp, raw_json, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key, canonical.store_id, canonical.event_type, canonical.visitor_id,
         ts, json.dumps(raw, default=str), now),
    )
    return cur.rowcount > 0


def event_count(conn: sqlite3.Connection, store_id: str = None) -> int:
    if store_id:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE store_id=?", (store_id,)
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def last_event_times(conn: sqlite3.Connection) -> dict:
    """Return {store_id: last_event_timestamp} for all stores."""
    rows = conn.execute(
        "SELECT store_id, MAX(timestamp) as last FROM events GROUP BY store_id"
    ).fetchall()
    return {r["store_id"]: r["last"] for r in rows}


def fetch_events(conn: sqlite3.Connection, store_id: str,
                 event_types: list = None) -> list:
    """Return raw_json dicts for a store, optionally filtered by event_type."""
    if event_types:
        placeholders = ",".join("?" * len(event_types))
        rows = conn.execute(
            f"SELECT raw_json FROM events WHERE store_id=? AND event_type IN ({placeholders})"
            " ORDER BY timestamp",
            [store_id] + list(event_types),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT raw_json FROM events WHERE store_id=? ORDER BY timestamp",
            (store_id,),
        ).fetchall()
    return [json.loads(r["raw_json"]) for r in rows]
