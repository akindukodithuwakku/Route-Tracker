"""
Sends aggregated usage batches to the manager over HTTPS, with a durable
local queue underneath so a temporarily-unreachable manager (rebooting,
network blip) never loses data -- everything is written to disk first and
only removed from the queue after the manager confirms receipt (HTTP 200).
"""

import logging
import threading
import time

import requests

import local_queue
from config import Config

log = logging.getLogger("reporter")

AGENT_VERSION = "0.1.0"
PROTOCOL_VERSION = 1

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 60.0
_SEND_TIMEOUT = 10.0


class Reporter:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._stop = threading.Event()
        self._backoff = _MIN_BACKOFF
        local_queue.init()

    def submit(self, records: list):
        """Called by the capture engine every flush interval. Durable-writes
        immediately; the sender thread picks it up asynchronously."""
        if not records:
            return
        batch = {
            "client_id": self._cfg.client_id,
            "hostname": self._cfg.hostname,
            "agent_version": AGENT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "records": [r.to_dict() for r in records],
        }
        local_queue.enqueue(batch, created_at=time.time())
        log.info("queued %d records (queue depth now %d)", len(records), local_queue.depth())

    def _send_batch(self, batch: dict) -> bool:
        try:
            resp = requests.post(
                self._cfg.manager_url,
                json=batch,
                headers={"X-API-Key": self._cfg.api_key},
                timeout=_SEND_TIMEOUT,
                verify=self._cfg.requests_verify,
            )
            if resp.status_code == 200:
                return True
            log.warning("manager rejected batch: HTTP %d %s", resp.status_code, resp.text[:200])
            return False
        except requests.RequestException as e:
            log.warning("send failed (manager unreachable?): %s", e)
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
