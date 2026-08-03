"""
Pure parsing helpers: pull a domain name out of the first few bytes of a TCP
flow, without needing to reassemble the whole session.

- TLS ClientHello (port 443, and increasingly other ports): reads the SNI
  (Server Name Indication) extension, which is sent in cleartext even for
  encrypted connections. This is the same mechanism corporate firewalls use.
- Plain HTTP request (port 80): reads the Host: header.

Both functions return None (never raise) when the payload isn't a match --
capture.py falls back to reverse DNS in that case.
"""

from typing import Optional

_HTTP_METHODS = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ", b"OPTIONS ", b"CONNECT ", b"PATCH ")


def parse_tls_client_hello_sni(payload: bytes) -> Optional[str]:
    try:
        if len(payload) < 5:
            return None
        # TLS record header: type(1) version(2) length(2)
        if payload[0] != 0x16:  # handshake
            return None
        pos = 5
        if len(payload) < pos + 4:
            return None
        # Handshake header: msg_type(1) length(3)
        if payload[pos] != 0x01:  # ClientHello
            return None
        pos += 4

        # ClientHello body: client_version(2) random(32)
        pos += 2 + 32
        if pos + 1 > len(payload):
            return None

        session_id_len = payload[pos]
        pos += 1 + session_id_len

        if pos + 2 > len(payload):
            return None
        cipher_suites_len = int.from_bytes(payload[pos:pos + 2], "big")
        pos += 2 + cipher_suites_len

        if pos + 1 > len(payload):
            return None
        compression_len = payload[pos]
        pos += 1 + compression_len

        if pos + 2 > len(payload):
            return None
        extensions_total_len = int.from_bytes(payload[pos:pos + 2], "big")
        pos += 2
        extensions_end = pos + extensions_total_len
        if extensions_end > len(payload):
            extensions_end = len(payload)  # be lenient; still try what we have

        while pos + 4 <= extensions_end:
            ext_type = int.from_bytes(payload[pos:pos + 2], "big")
            ext_len = int.from_bytes(payload[pos + 2:pos + 4], "big")
            ext_data_start = pos + 4
            ext_data_end = ext_data_start + ext_len
            if ext_data_end > len(payload):
                return None

            if ext_type == 0x0000:  # server_name
                sni_pos = ext_data_start + 2  # server_name_list_length
                if sni_pos + 3 > len(payload):
                    return None
                name_type = payload[sni_pos]
                name_len = int.from_bytes(payload[sni_pos + 1:sni_pos + 3], "big")
                name_start = sni_pos + 3
                name_end = name_start + name_len
                if name_type != 0 or name_end > len(payload):
                    return None
                return payload[name_start:name_end].decode("ascii", errors="ignore") or None

            pos = ext_data_end

        return None
    except (IndexError, ValueError):
        return None


def parse_http_host(payload: bytes) -> Optional[str]:
    try:
        if not payload.startswith(_HTTP_METHODS):
            return None
        lines = payload.split(b"\r\n")
        for line in lines[1:]:
            if line[:5].lower() == b"host:":
                host = line[5:].strip().decode("ascii", errors="ignore")
                return host.split(":")[0] if host else None  # strip a :port suffix
        return None
    except (IndexError, ValueError):
        return None
