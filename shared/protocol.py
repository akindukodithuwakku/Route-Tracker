"""
The record shape the agent produces and POSTs to the cloud /report endpoint.
Kept in its own module so the field names stay in one place -- the server side
of this contract lives in functions/src/index.ts.
"""

from dataclasses import dataclass, asdict
from typing import Optional


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
