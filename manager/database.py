"""SQLite storage for the manager. One file, no external DB server needed."""

import sqlite3
import threading
from contextlib import contextmanager

from paths import base_dir

DB_PATH = base_dir() / "data" / "usage.db"

_lock = threading.Lock()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                client_id   TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                hostname    TEXT,
                last_seen   TEXT
            );

            CREATE TABLE IF NOT EXISTS usage_records (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       TEXT NOT NULL,
                domain          TEXT NOT NULL,
                process_name    TEXT,
                bytes_sent      INTEGER NOT NULL,
                bytes_received  INTEGER NOT NULL,
                started_at      TEXT NOT NULL,
                ended_at        TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                received_at     TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
            );

            CREATE INDEX IF NOT EXISTS idx_usage_client_time
                ON usage_records(client_id, started_at);

            CREATE INDEX IF NOT EXISTS idx_usage_domain
                ON usage_records(domain);
            """
        )


@contextmanager
def get_conn():
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def upsert_client(client_id: str, display_name: str, hostname: str, last_seen: str):
    """Full upsert used when a real report comes in -- always reflects the
    latest hostname/last_seen."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO clients (client_id, display_name, hostname, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                hostname = excluded.hostname,
                last_seen = excluded.last_seen
            """,
            (client_id, display_name, hostname, last_seen),
        )


def ensure_client_row(client_id: str, display_name: str):
    """Registers a configured client so it shows up on the dashboard before
    its first report arrives, WITHOUT clobbering hostname/last_seen if a row
    (and history) already exists from a previous run."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO clients (client_id, display_name, hostname, last_seen) "
            "VALUES (?, ?, NULL, NULL)",
            (client_id, display_name),
        )


def insert_records(client_id: str, records: list, received_at: str):
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO usage_records
                (client_id, domain, process_name, bytes_sent, bytes_received,
                 started_at, ended_at, duration_seconds, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    client_id,
                    r["domain"],
                    r.get("process_name"),
                    r["bytes_sent"],
                    r["bytes_received"],
                    r["started_at"],
                    r["ended_at"],
                    r["duration_seconds"],
                    received_at,
                )
                for r in records
            ],
        )


def list_clients():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM clients ORDER BY display_name")]


def summary_by_client(since_iso: str):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.client_id, c.display_name, c.hostname, c.last_seen,
                   COALESCE(SUM(u.bytes_sent), 0)     AS bytes_sent,
                   COALESCE(SUM(u.bytes_received), 0) AS bytes_received,
                   COALESCE(SUM(u.duration_seconds), 0) AS active_seconds,
                   COUNT(DISTINCT u.domain) AS distinct_domains
            FROM clients c
            LEFT JOIN usage_records u
                ON u.client_id = c.client_id AND u.started_at >= ?
            GROUP BY c.client_id
            ORDER BY c.display_name
            """,
            (since_iso,),
        )
        return [dict(row) for row in rows]


def top_domains(since_iso: str, client_id: str = None, limit: int = 15):
    with get_conn() as conn:
        if client_id:
            rows = conn.execute(
                """
                SELECT domain,
                       SUM(bytes_sent + bytes_received) AS total_bytes,
                       SUM(duration_seconds) AS total_seconds
                FROM usage_records
                WHERE started_at >= ? AND client_id = ?
                GROUP BY domain
                ORDER BY total_bytes DESC
                LIMIT ?
                """,
                (since_iso, client_id, limit),
            )
        else:
            rows = conn.execute(
                """
                SELECT domain,
                       SUM(bytes_sent + bytes_received) AS total_bytes,
                       SUM(duration_seconds) AS total_seconds
                FROM usage_records
                WHERE started_at >= ?
                GROUP BY domain
                ORDER BY total_bytes DESC
                LIMIT ?
                """,
                (since_iso, limit),
            )
        return [dict(row) for row in rows]


def timeseries(since_iso: str, client_id: str = None, bucket_minutes: int = 30):
    """Bandwidth bucketed into fixed-size time windows for charting."""
    with get_conn() as conn:
        params = [since_iso]
        client_filter = ""
        if client_id:
            client_filter = "AND client_id = ?"
            params.append(client_id)
        rows = conn.execute(
            f"""
            SELECT started_at, bytes_sent, bytes_received
            FROM usage_records
            WHERE started_at >= ? {client_filter}
            ORDER BY started_at
            """,
            params,
        )
        return [dict(row) for row in rows]
