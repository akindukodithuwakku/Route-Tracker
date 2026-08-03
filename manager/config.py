"""
Client registry for the manager.

config/clients.json holds one entry per client PC: a stable client_id, a
human-readable display_name, and a per-client API key. The same client_id +
api_key pair must be copied into that PC's client_agent/config.json so the
manager can authenticate reports and tell the 5 PCs apart on the dashboard.

If config/clients.json doesn't exist yet, we generate one with 5 placeholder
entries and fresh random API keys on first run -- rename the display_names,
then copy each client_id/api_key pair out to the matching PC.
"""

import json
import secrets

from paths import base_dir

CONFIG_PATH = base_dir() / "config" / "clients.json"


def _default_config():
    return {
        "clients": [
            {
                "client_id": f"pc{i}",
                "display_name": f"Client PC {i}",
                "api_key": secrets.token_hex(24),
            }
            for i in range(1, 6)
        ]
    }


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = _default_config()
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        return cfg
    return json.loads(CONFIG_PATH.read_text())


def clients_by_api_key():
    cfg = load_config()
    return {c["api_key"]: c for c in cfg["clients"]}


def clients_by_id():
    cfg = load_config()
    return {c["client_id"]: c for c in cfg["clients"]}
