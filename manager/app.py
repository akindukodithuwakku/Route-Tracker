"""
Manager server: receives usage reports from the 5 client agents and serves
the localhost dashboard. Run with:

    py -m uvicorn app:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem

(see docs/SETUP.md for cert generation and the plain-HTTP dev shortcut).
"""

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

import database
import config
from paths import bundle_dir

app = FastAPI(title="LAN Usage Monitor - Manager")

STATIC_DIR = bundle_dir() / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup():
    database.init_db()
    # Make sure every configured client has a row so it shows up on the
    # dashboard immediately, even before its first report arrives.
    for c in config.load_config()["clients"]:
        database.ensure_client_row(c["client_id"], c["display_name"])


# ---------- ingest ----------

class UsageRecordIn(BaseModel):
    domain: str
    process_name: Optional[str] = None
    bytes_sent: int
    bytes_received: int
    started_at: str
    ended_at: str
    duration_seconds: float


class ReportBatchIn(BaseModel):
    client_id: str
    hostname: str
    agent_version: str
    protocol_version: int
    records: List[UsageRecordIn]


def _authenticate(client_id: str, x_api_key: Optional[str]):
    clients = config.clients_by_api_key()
    entry = clients.get(x_api_key)
    if entry is None or entry["client_id"] != client_id:
        raise HTTPException(status_code=401, detail="invalid client_id / API key")
    return entry


@app.post("/api/report")
def report(batch: ReportBatchIn, x_api_key: Optional[str] = Header(None)):
    entry = _authenticate(batch.client_id, x_api_key)

    now = datetime.now(timezone.utc).isoformat()
    database.upsert_client(entry["client_id"], entry["display_name"], batch.hostname, now)

    if batch.records:
        database.insert_records(
            entry["client_id"],
            [r.dict() for r in batch.records],
            received_at=now,
        )

    return {"status": "ok", "accepted": len(batch.records)}


# ---------- dashboard API ----------

_RANGES = {
    "1h": timedelta(hours=1),
    "today": None,  # handled specially: since midnight local/UTC
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _since_iso(range_key: str) -> str:
    now = datetime.now(timezone.utc)
    if range_key == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        delta = _RANGES.get(range_key, timedelta(hours=24))
        since = now - delta
    return since.isoformat()


@app.get("/api/clients")
def api_clients():
    clients = database.list_clients()
    now = datetime.now(timezone.utc)
    for c in clients:
        if c["last_seen"]:
            last_seen = datetime.fromisoformat(c["last_seen"])
            c["online"] = (now - last_seen) < timedelta(minutes=3)
        else:
            c["online"] = False
    return clients


@app.get("/api/stats/summary")
def api_summary(range: str = "today"):
    return database.summary_by_client(_since_iso(range))


@app.get("/api/stats/top-domains")
def api_top_domains(range: str = "today", client_id: Optional[str] = None, limit: int = 15):
    return database.top_domains(_since_iso(range), client_id=client_id, limit=limit)


@app.get("/api/stats/timeseries")
def api_timeseries(range: str = "today", client_id: Optional[str] = None):
    return database.timeseries(_since_iso(range), client_id=client_id)


@app.get("/")
def dashboard():
    return FileResponse(str(STATIC_DIR / "index.html"))
