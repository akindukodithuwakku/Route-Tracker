"""Manager server listen settings (host/port/TLS cert paths)."""

import json

from paths import base_dir

CONFIG_PATH = base_dir() / "config" / "server.json"
CERT_DIR = base_dir() / "certs"

_DEFAULT = {
    "host": "0.0.0.0",
    "port": 8443,
    "ssl_certfile": str(CERT_DIR / "cert.pem"),
    "ssl_keyfile": str(CERT_DIR / "key.pem"),
}


def load_server_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(_DEFAULT, indent=2))
        return dict(_DEFAULT)
    return json.loads(CONFIG_PATH.read_text())
