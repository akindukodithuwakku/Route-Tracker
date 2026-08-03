"""
Runs the manager (uvicorn + app.app) with a controllable stop, so both a
plain `py run_server.py` and the Windows Service (service.py) share one
code path. Falls back to plain HTTP if no cert is present yet, so the
dashboard is usable immediately after `py generate_cert.py` is skipped
during first-time local testing -- but real client agents require HTTPS
(see docs/SETUP.md), so generate the cert before deploying agents.
"""

import logging
import threading
from pathlib import Path

import uvicorn

import database
import app as app_module
from server_config import load_server_config

log = logging.getLogger("manager")


def run_forever(stop_event: threading.Event = None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stop_event = stop_event or threading.Event()

    database.init_db()
    cfg = load_server_config()

    kwargs = dict(host=cfg["host"], port=cfg["port"])
    cert = Path(cfg["ssl_certfile"])
    key = Path(cfg["ssl_keyfile"])
    if cert.exists() and key.exists():
        kwargs["ssl_certfile"] = str(cert)
        kwargs["ssl_keyfile"] = str(key)
        log.info("starting manager on https://%s:%s", cfg["host"], cfg["port"])
    else:
        log.warning("no TLS cert found at %s -- starting on PLAIN HTTP. Run generate_cert.py "
                     "before deploying real client agents.", cert)
        log.info("starting manager on http://%s:%s", cfg["host"], cfg["port"])

    # Pass the app object directly (not the "app:app" import string) so
    # PyInstaller's static analysis sees the real `import app` above --
    # a string target would rely on an import uvicorn does dynamically at
    # runtime, which a frozen build's bundler can't see ahead of time.
    uv_config = uvicorn.Config(app_module.app, **kwargs, log_level="info")
    server = uvicorn.Server(uv_config)

    def watch_stop():
        stop_event.wait()
        server.should_exit = True

    threading.Thread(target=watch_stop, daemon=True).start()
    server.run()


if __name__ == "__main__":
    run_forever()
