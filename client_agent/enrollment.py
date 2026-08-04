"""
Turns the one shared enrollment token into this PC's own device_id/device_key,
the first time the agent runs. Everything after that reads credentials.json --
enrollment never repeats unless that file is deleted.

Re-running enroll with the same machine_guid (a reinstall, or credentials.json
lost) returns the SAME device server-side rather than creating a duplicate
card on the dashboard, and rotates the key.
"""

import json
import logging
import threading
import winreg

import requests

from config import Config
from paths import base_dir

log = logging.getLogger("enrollment")

CREDENTIALS_PATH = base_dir() / "credentials.json"

_ENROLL_TIMEOUT = 15.0
_RETRY_DELAY = 30.0


def _machine_guid() -> str:
    """Stable per-Windows-install identifier, persists across this agent being
    reinstalled (unlike a freshly generated file-based id)."""
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
    try:
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return value
    finally:
        winreg.CloseKey(key)


def _load_saved():
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        if data.get("device_id") and data.get("device_key"):
            return data["device_id"], data["device_key"]
    except (json.JSONDecodeError, OSError):
        pass
    return None


def get_or_create_credentials(cfg: Config, stop_event: threading.Event = None):
    """Returns (device_id, device_key), enrolling with the cloud on first call,
    or None if stop_event is set while retrying. Blocks with retries if the
    cloud isn't reachable yet -- there is nothing useful to do until this
    succeeds at least once, but retries respect stop_event so a service stop
    isn't stuck behind a blind sleep."""
    saved = _load_saved()
    if saved:
        return saved

    stop_event = stop_event or threading.Event()
    guid = _machine_guid()
    payload = {
        "enrollment_token": cfg.enrollment_token,
        "hostname": cfg.hostname,
        "machine_guid": guid,
    }

    while not stop_event.is_set():
        try:
            resp = requests.post(f"{cfg.cloud_base_url}/enroll", json=payload, timeout=_ENROLL_TIMEOUT)
        except requests.RequestException as e:
            log.warning("enrollment request failed (%s); retrying in %ss", e, _RETRY_DELAY)
            stop_event.wait(_RETRY_DELAY)
            continue

        if resp.status_code == 200:
            data = resp.json()
            CREDENTIALS_PATH.write_text(json.dumps(data, indent=2))
            log.info("enrolled as device_id=%s", data["device_id"])
            return data["device_id"], data["device_key"]

        if resp.status_code == 401:
            # Bad token: retrying won't help without a human fixing config.json.
            raise RuntimeError(
                f"enrollment rejected (401): check enrollment_token in {base_dir() / 'config.json'}"
            )

        log.warning("enrollment failed: HTTP %s %s; retrying in %ss", resp.status_code, resp.text[:200], _RETRY_DELAY)
        stop_event.wait(_RETRY_DELAY)

    return None
