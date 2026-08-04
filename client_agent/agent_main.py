"""
Client agent entry point: loads config, wires the packet-capture engine to
the reporter, and runs until stopped. This is what both the interactive
`py agent_main.py` run and the Windows Service (service.py) actually execute.

Designed to never crash out permanently: any exception in the run loop is
logged and the capture engine is restarted after a short delay, so a
transient WinDivert hiccup doesn't take the agent down for good. That's the
"should always run" requirement -- the outer Windows Service adds the
second layer (restart the whole process if Python itself dies).
"""

import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from aggregator import Aggregator
from capture import CaptureEngine
from enrollment import get_or_create_credentials
from reporter import Reporter
from paths import base_dir

log = logging.getLogger("agent")

_RESTART_DELAY = 10.0


def _setup_logging():
    log_dir = base_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "agent.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def run_forever(stop_event=None):
    """Runs until stop_event is set (or forever, for a plain script run).
    stop_event lets service.py signal a clean shutdown on service stop."""
    _setup_logging()
    stop_event = stop_event or _default_stop_event()

    cfg = load_config()
    log.info("agent starting: cloud=%s", cfg.cloud_base_url)

    credentials = get_or_create_credentials(cfg, stop_event)
    if credentials is None:
        log.info("stopped before enrollment completed")
        return
    device_id, device_key = credentials

    reporter = Reporter(cfg, device_id, device_key)
    reporter.start()

    while not stop_event.is_set():
        aggregator = Aggregator()
        engine = CaptureEngine(
            aggregator,
            exclude_domains=cfg.exclude_domains,
            flush_interval=cfg.report_interval_seconds,
            on_flush=reporter.submit,
        )
        try:
            log.info("capture engine starting")
            _run_until_stopped(engine, stop_event)
        except Exception:
            log.exception("capture engine crashed; restarting in %ss", _RESTART_DELAY)
            stop_event.wait(_RESTART_DELAY)

    reporter.stop()
    log.info("agent stopped")


def _run_until_stopped(engine: CaptureEngine, stop_event):
    import threading

    error_box = {}

    def target():
        try:
            engine.run()
        except Exception as e:  # noqa: BLE001 -- re-raised on the caller's thread below
            error_box["exc"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    while t.is_alive() and not stop_event.is_set():
        stop_event.wait(1.0)
    engine.stop()
    t.join(timeout=5.0)

    # engine.run() executes on a background thread, so a crash there (e.g.
    # WinDivert failing to open) would otherwise go unnoticed here -- the
    # thread just dies quietly and the while loop above exits immediately,
    # which skipped the crash-restart delay entirely and hot-looped. Re-raise
    # on this thread so the caller's except/backoff actually applies.
    if "exc" in error_box:
        raise error_box["exc"]


def _default_stop_event():
    import threading
    ev = threading.Event()

    def handle_signal(signum, frame):
        log.info("received signal %s, shutting down", signum)
        ev.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    return ev


if __name__ == "__main__":
    run_forever()
