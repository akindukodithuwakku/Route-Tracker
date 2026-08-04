"""
Per-PC agent configuration. config.json holds only what's the SAME on every
PC: the cloud endpoint and the shared enrollment token (see
docs/SETUP.md / scripts/setup-project.js). The installer writes it once from
two pasted values -- there is no per-PC id or key to configure by hand,
that's what enrollment.py obtains automatically on first run.

If config.json is missing, we write a placeholder template and raise, so a
misconfigured install fails loudly at startup instead of silently reporting
nothing.
"""

import json
import socket
from urllib.parse import urlparse

from paths import base_dir

CONFIG_PATH = base_dir() / "config.json"

_TEMPLATE = {
    "cloud_base_url": "https://asia-south1-REPLACE_ME.cloudfunctions.net",
    "enrollment_token": "REPLACE_ME_WITH_TOKEN_FROM_SETUP_SCRIPT",
    "report_interval_seconds": 180,
}


class Config:
    def __init__(self, data: dict):
        self.cloud_base_url = data["cloud_base_url"].rstrip("/")
        self.enrollment_token = data["enrollment_token"]
        self.report_interval_seconds = data.get("report_interval_seconds", 180)
        self.hostname = socket.gethostname()

        # The agent's own reports to this host must not be counted as "usage" --
        # matched by domain (SNI) rather than port, since the endpoint is a
        # normal HTTPS host sharing port 443 with everything else.
        cloud_host = urlparse(self.cloud_base_url).hostname
        self.exclude_domains = {cloud_host} if cloud_host else set()


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(_TEMPLATE, indent=2))
        raise RuntimeError(
            f"No config.json found -- wrote a template to {CONFIG_PATH}. "
            "Fill in cloud_base_url and enrollment_token, then restart."
        )
    data = json.loads(CONFIG_PATH.read_text())
    if data.get("enrollment_token") == _TEMPLATE["enrollment_token"]:
        raise RuntimeError(f"{CONFIG_PATH} still has placeholder values -- edit it first.")
    return Config(data)
