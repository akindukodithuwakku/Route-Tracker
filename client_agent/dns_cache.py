"""
IP → human-readable name resolution. Zero cloud cost: everything runs on the
client PC using the OS resolver and names learned from DNS answers we already
see on the wire.

Priority:
  1. Forward cache from snooped DNS A/AAAA responses (best signal -- this is
     the name the browser actually looked up)
  2. Reverse-DNS PTR via the OS (free, often missing/useless for CDNs)
  3. None -- caller keeps the raw IP

Caches both hits and misses so a flood of connections to one IP doesn't turn
into a flood of blocking DNS lookups.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Optional

_HIT_TTL = 6 * 3600
_MISS_TTL = 5 * 60
_FORWARD_TTL_CAP = 6 * 3600  # don't honor DNS TTLs longer than this

_forward: dict[str, tuple[str, float]] = {}  # ip -> (name, expires)
_reverse: dict[str, tuple[Optional[str], float]] = {}  # ip -> (name|None, expires)
_lock = threading.Lock()


def learn(ip: str, name: str, ttl: int = 300) -> None:
    """Record that `ip` was answered for DNS name `name`."""
    if not ip or not name:
        return
    name = name.rstrip(".").lower()
    if not name or name == ip:
        return
    expires = time.time() + max(30, min(int(ttl), _FORWARD_TTL_CAP))
    with _lock:
        _forward[ip] = (name, expires)


def peek_forward(ip: str) -> Optional[str]:
    """Non-blocking: return a snooped forward name if still cached."""
    now = time.time()
    with _lock:
        fwd = _forward.get(ip)
        if fwd and fwd[1] > now:
            return fwd[0]
    return None


def lookup(ip: str) -> Optional[str]:
    """Return a hostname for `ip`, or None if we only know the raw address."""
    now = time.time()
    with _lock:
        fwd = _forward.get(ip)
        if fwd and fwd[1] > now:
            return fwd[0]
        rev = _reverse.get(ip)
        if rev and rev[1] > now:
            return rev[0]  # may be None (cached miss)

    # No forward hit: try PTR once (result cached, including misses).
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        result: Optional[str] = host.rstrip(".").lower() if host else None
        if result == ip:
            result = None
        ttl = _HIT_TTL if result else _MISS_TTL
    except (socket.herror, socket.gaierror, OSError):
        result = None
        ttl = _MISS_TTL

    with _lock:
        # A snooped forward name that arrived while we blocked wins.
        fwd = _forward.get(ip)
        if fwd and fwd[1] > time.time():
            return fwd[0]
        _reverse[ip] = (result, time.time() + ttl)
    return result


def reverse_dns(ip: str) -> str:
    """Back-compat helper: hostname or the original IP when unknown."""
    return lookup(ip) or ip


def learn_from_dns_payload(payload: bytes) -> int:
    """
    Parse a DNS response payload and learn A/AAAA → name mappings.
    Returns how many address records were learned. Safe on garbage input.
    """
    if not payload or len(payload) < 12:
        return 0
    try:
        return _parse_dns_response(payload)
    except Exception:  # noqa: BLE001 -- never let a bad packet kill capture
        return 0


def _parse_dns_response(payload: bytes) -> int:
    flags = struct.unpack("!H", payload[2:4])[0]
    if (flags & 0x8000) == 0:
        return 0  # not a response
    if (flags & 0x000F) != 0:
        return 0  # RCODE != NOERROR
    qdcount, ancount = struct.unpack("!HH", payload[4:8])
    if ancount == 0:
        return 0

    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(payload, offset)
        offset += 4  # qtype + qclass
        if offset > len(payload):
            return 0

    learned = 0
    for _ in range(ancount):
        name, offset = _read_name(payload, offset)
        if offset + 10 > len(payload):
            break
        rtype, _rclass, ttl, rdlength = struct.unpack("!HHIH", payload[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(payload):
            break
        rdata = payload[offset : offset + rdlength]
        offset += rdlength

        if rtype == 1 and rdlength == 4:  # A
            ip = socket.inet_ntop(socket.AF_INET, rdata)
            learn(ip, name, ttl)
            learned += 1
        elif rtype == 28 and rdlength == 16:  # AAAA
            ip = socket.inet_ntop(socket.AF_INET6, rdata)
            learn(ip, name, ttl)
            learned += 1

    return learned


def _skip_name(buf: bytes, offset: int) -> int:
    while offset < len(buf):
        length = buf[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:  # compression pointer
            return offset + 2
        offset += 1 + length
    return offset


def _read_name(buf: bytes, offset: int) -> tuple[str, int]:
    labels = []
    jumped = False
    end = offset
    guard = 0
    while offset < len(buf) and guard < 64:
        guard += 1
        length = buf[offset]
        if length == 0:
            if not jumped:
                end = offset + 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(buf):
                break
            pointer = ((length & 0x3F) << 8) | buf[offset + 1]
            if not jumped:
                end = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        labels.append(buf[offset : offset + length].decode("ascii", errors="ignore"))
        offset += length
        if not jumped:
            end = offset
    return ".".join(labels), end
