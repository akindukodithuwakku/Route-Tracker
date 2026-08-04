"""
Pure in-memory bookkeeping: turns a stream of "packet seen" events into
periodic UsageRecord batches. Deliberately has zero dependency on pydivert/
WinDivert so it can be unit-tested without admin rights or a real NIC --
capture.py is the thin adapter that feeds it real packets.
"""

import sys
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
from protocol import UsageRecord  # noqa: E402

FlowKey = namedtuple("FlowKey", ["proto", "local_ip", "local_port", "remote_ip", "remote_port"])

FLOW_IDLE_TIMEOUT = 90.0  # drop a flow if it's been silent this long


class _FlowState:
    __slots__ = (
        "domain", "domain_resolved", "process_name",
        "bytes_sent", "bytes_received",
        "window_start", "last_seen",
    )

    def __init__(self, now: float):
        self.domain = None
        self.domain_resolved = False
        self.process_name = None
        self.bytes_sent = 0
        self.bytes_received = 0
        self.window_start = now
        self.last_seen = now


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class Aggregator:
    def __init__(self):
        self._flows = {}

    def on_packet(self, flow_key: FlowKey, direction: str, nbytes: int, now: float = None,
                   domain: str = None, process_name: str = None):
        """direction is 'sent' (outbound) or 'received' (inbound)."""
        now = now if now is not None else time.time()
        flow = self._flows.get(flow_key)
        if flow is None:
            flow = _FlowState(now)
            self._flows[flow_key] = flow

        if direction == "sent":
            flow.bytes_sent += nbytes
        else:
            flow.bytes_received += nbytes
        flow.last_seen = now

        if domain:
            # Allow upgrading a temporary IP label to a real hostname if DNS
            # snooping/PTR lands after the first packets of the flow.
            if not flow.domain_resolved or self._is_ip(flow.domain):
                flow.domain = domain
                flow.domain_resolved = not self._is_ip(domain)
        if process_name and not flow.process_name:
            flow.process_name = process_name

    def resolve_pending(self, flow_key: FlowKey, domain: str):
        """Called once an async DNS lookup completes for a flow that had no
        SNI/Host domain at packet time. Ignores results that are still IPs."""
        if not domain or self._is_ip(domain):
            return
        flow = self._flows.get(flow_key)
        if flow and (not flow.domain_resolved or self._is_ip(flow.domain)):
            flow.domain = domain
            flow.domain_resolved = True

    def unresolved_flows(self):
        return [
            k
            for k, f in self._flows.items()
            if not f.domain_resolved or self._is_ip(f.domain)
        ]

    @staticmethod
    def _is_ip(value):
        if not value:
            return False
        try:
            import ipaddress

            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    def flush(self, now: float = None) -> list:
        """Emit one UsageRecord per (domain, process) grouping seen since the
        last flush, drop long-idle flows, and reset per-flow counters for the
        flows that stay alive."""
        now = now if now is not None else time.time()
        buckets = {}  # (domain, process_name) -> UsageRecord accumulator
        dead_keys = []

        for key, flow in self._flows.items():
            if now - flow.last_seen > FLOW_IDLE_TIMEOUT:
                dead_keys.append(key)
                if flow.bytes_sent == 0 and flow.bytes_received == 0:
                    continue  # nothing to report, just drop it

            if flow.bytes_sent == 0 and flow.bytes_received == 0:
                continue

            domain = flow.domain or key.remote_ip
            bucket_key = (domain, flow.process_name)
            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "bytes_sent": 0,
                    "bytes_received": 0,
                    "started_at": flow.window_start,
                    "ended_at": flow.last_seen,
                }
            b = buckets[bucket_key]
            b["bytes_sent"] += flow.bytes_sent
            b["bytes_received"] += flow.bytes_received
            b["started_at"] = min(b["started_at"], flow.window_start)
            b["ended_at"] = max(b["ended_at"], flow.last_seen)

            flow.bytes_sent = 0
            flow.bytes_received = 0
            flow.window_start = now

        for key in dead_keys:
            del self._flows[key]

        records = []
        for (domain, process_name), b in buckets.items():
            records.append(UsageRecord(
                domain=domain,
                process_name=process_name,
                bytes_sent=b["bytes_sent"],
                bytes_received=b["bytes_received"],
                started_at=_iso(b["started_at"]),
                ended_at=_iso(b["ended_at"]),
                duration_seconds=max(0.0, b["ended_at"] - b["started_at"]),
            ))
        return records
