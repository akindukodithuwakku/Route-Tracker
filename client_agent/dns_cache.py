"""
Reverse-DNS fallback for traffic where we couldn't read a domain from SNI/Host
(e.g. QUIC/UDP, or a ClientHello split across packets). Best-effort only --
many residential/cloud IPs have no reverse record, in which case we fall back
to the raw IP so bandwidth still gets attributed to *something*.

Caches both hits and misses (with separate TTLs) so a flood of connections to
one IP doesn't turn into a flood of blocking DNS lookups.
"""

import socket
import time
import threading

_HIT_TTL = 6 * 3600
_MISS_TTL = 5 * 60

_cache = {}
_lock = threading.Lock()


def reverse_dns(ip: str) -> str:
    now = time.time()
    with _lock:
        entry = _cache.get(ip)
        if entry and entry[1] > now:
            return entry[0]

    try:
        host, _, _ = socket.gethostbyaddr(ip)
        result = host
        ttl = _HIT_TTL
    except (socket.herror, socket.gaierror, OSError):
        result = ip
        ttl = _MISS_TTL

    with _lock:
        _cache[ip] = (result, now + ttl)
    return result
