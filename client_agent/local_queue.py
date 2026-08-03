"""
Durable on-disk queue for outgoing report batches. A batch is written here
the instant it's produced, *before* we try to send it -- so if the manager
PC is rebooting, unplugged, or the network is down, nothing is lost. The
background sender in reporter.py drains this queue in order once the
manager is reachable again.
"""

import json
import sqlite3
import threading

from paths import base_dir

DB_PATH = base_dir() / "data" / "queue.db"

_lock = threading.Lock()


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )


def _connect():
    return sqlite3.connect(DB_PATH, timeout=30)


def enqueue(batch_dict: dict, created_at: float):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO pending_batches (payload_json, created_at) VALUES (?, ?)",
            (json.dumps(batch_dict), created_at),
        )
        conn.commit()


def peek_oldest(limit: int = 20):
    """Returns up to `limit` oldest (id, batch_dict) pairs without removing them."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, payload_json FROM pending_batches ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [(row[0], json.loads(row[1])) for row in rows]


def delete(ids: list):
    if not ids:
        return
    with _lock, _connect() as conn:
        conn.executemany("DELETE FROM pending_batches WHERE id = ?", [(i,) for i in ids])
        conn.commit()


def depth() -> int:
    with _lock, _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM pending_batches").fetchone()[0]
