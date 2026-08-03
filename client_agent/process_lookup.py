"""
Maps a local (protocol, port) pair to the process name that owns it, so usage
records can show "chrome.exe" instead of just a domain. Best-effort: the
Windows connection table is refreshed on a short interval rather than per
packet (querying it on every packet would be far too slow), so a connection
that opens and closes within that window may go unattributed.

Requires the agent to run elevated (which it does, as a Windows Service under
LocalSystem) to see connections owned by other users' processes.
"""

import socket
import threading
import time

import psutil

_REFRESH_INTERVAL = 2.0


class ProcessLookup:
    def __init__(self):
        self._lock = threading.Lock()
        self._table = {}  # (proto, local_port) -> process_name
        self._last_refresh = 0.0

    def _refresh(self):
        table = {}
        try:
            for conn in psutil.net_connections(kind="inet"):
                if not conn.laddr or conn.pid is None:
                    continue
                proto = "tcp" if conn.type == socket.SOCK_STREAM else "udp"
                key = (proto, conn.laddr.port)
                if key in table:
                    continue
                try:
                    table[key] = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except psutil.AccessDenied:
            pass  # not running elevated; process attribution will just be empty
        self._table = table
        self._last_refresh = time.monotonic()

    def get_process_name(self, proto: str, local_port: int):
        with self._lock:
            if time.monotonic() - self._last_refresh > _REFRESH_INTERVAL:
                self._refresh()
            return self._table.get((proto, local_port))
