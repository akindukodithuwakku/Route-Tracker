"""
Sends aggregated usage batches to the cloud, with a durable local queue
underneath so a temporarily-unreachable network never loses data --
everything is written to disk first and only removed from the queue after
the server confirms receipt (HTTP 200).
"""

import logging
import threading
import time
from datetime import datetime

import requests

import local_queue
from config import Config

log = logging.getLogger("reporter")

AGENT_VERSION = "0.2.0"

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0
_SEND_TIMEOUT = 15.0


def _tz_offset_minutes() -> int:
    """Current UTC offset for this PC's local clock, DST-aware."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


class Reporter:
    def __init__(self, cfg: Config, device_id: str, device_key: str):
        self._cfg = cfg
        self._device_id = device_id
        self._device_key = device_key
        self._stop = threading.Event()
        self._backoff = _MIN_BACKOFF
        local_queue.init()

    def submit(self, records: list):
        """Called by the capture engine every flush interval. Durable-writes
        immediately; the sender thread picks it up asynchronously."""
        if not records:
            return
        batch = {
            "device_id": self._device_id,
            "device_key": self._device_key,
            "agent_version": AGENT_VERSION,
            "tz_offset_minutes": _tz_offset_minutes(),
            "records": [r.to_dict() for r in records],
        }
        local_queue.enqueue(batch, created_at=time.time())
        log.info("queued %d records (queue depth now %d)", len(records), local_queue.depth())

    def _send_batch(self, batch: dict) -> bool:
        try:
            resp = requests.post(
                f"{self._cfg.cloud_base_url}/report",
                json=batch,
                timeout=_SEND_TIMEOUT,
            )
            if resp.status_code == 200:
                return True
            log.warning("server rejected batch: HTTP %d %s", resp.status_code, resp.text[:200])
            return False
        except requests.RequestException as e:
            log.warning("send failed (offline?): %s", e)
            return False

    def _drain_loop(self):
        while not self._stop.is_set():
            batches = local_queue.peek_oldest(limit=20)
            if not batches:
                self._stop.wait(2.0)
                continue

            sent_ids = []
            all_ok = True
            for batch_id, batch in batches:
                if self._send_batch(batch):
                    sent_ids.append(batch_id)
                else:
                    all_ok = False
                    break  # preserve order: stop at first failure, retry later

            if sent_ids:
                local_queue.delete(sent_ids)
                self._backoff = _MIN_BACKOFF

            if not all_ok:
                self._stop.wait(self._backoff)
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF)

    def start(self):
        threading.Thread(target=self._drain_loop, daemon=True, name="reporter-sender").start()

    def stop(self):
        self._stop.set()
