"""
Shared data contract between the client agent and the manager server.
Both sides import this module (client_agent copies it locally at install time,
see docs/SETUP.md) so the JSON shape never drifts out of sync.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

PROTOCOL_VERSION = 1


@dataclass
class UsageRecord:
    """One aggregated flow: a single domain talked to during one reporting window."""

    domain: str                    # e.g. "www.example.com" (from TLS SNI / HTTP Host / reverse DNS)
    process_name: Optional[str]    # e.g. "chrome.exe", None if it couldn't be attributed
    bytes_sent: int
    bytes_received: int
    started_at: str                # ISO-8601 UTC timestamp, first packet seen this window
    ended_at: str                  # ISO-8601 UTC timestamp, last packet seen this window
    duration_seconds: float        # wall-clock span of activity for this domain this window

    def to_dict(self):
        return asdict(self)


@dataclass
class ReportBatch:
    """What an agent POSTs to /api/report."""

    client_id: str          # stable id assigned to this PC (see manager/config/clients.json)
    hostname: str           # Windows hostname, for display / sanity-checking
    agent_version: str
    protocol_version: int
    records: list           # list[UsageRecord] (as dicts on the wire)

    def to_dict(self):
        d = asdict(self)
        d["records"] = [r.to_dict() if isinstance(r, UsageRecord) else r for r in self.records]
        return d
