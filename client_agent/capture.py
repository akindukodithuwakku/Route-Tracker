"""
Live packet capture via WinDivert (through the pydivert binding). This is the
only module that touches the network driver -- everything else is pure logic
so it stays testable off-box.

Requires:
  - Npcap or the WinDivert driver installed (see docs/SETUP.md)
  - Administrator / LocalSystem privileges (the Windows Service runs as
    LocalSystem, which covers this)

QUIC (HTTP/3 over UDP 443) traffic is counted for bandwidth but its domain
cannot be read the same way TLS-over-TCP can; it gets the reverse-DNS
fallback like any other unresolved flow.
"""

import ipaddress
import threading
import time

import pydivert

from aggregator import Aggregator, FlowKey
from dns_cache import reverse_dns
from process_lookup import ProcessLookup
import sni as sni_parser

# Only inspect these ports for cleartext-domain hints; everything else still
# gets counted for bandwidth, just attributed by IP/reverse-DNS.
_TLS_PORTS = {443}
_HTTP_PORTS = {80}

_RDNS_WORKERS = 4


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


class CaptureEngine:
    def __init__(self, aggregator: Aggregator, exclude_ports=(), flush_interval=30.0, on_flush=None):
        """
        exclude_ports: remote TCP ports to ignore entirely (use this to
        exclude the manager's own report port, so the agent doesn't try to
        classify/measure its own outgoing reports as "usage").
        """
        self._aggregator = aggregator
        self._exclude_ports = set(exclude_ports)
        self._flush_interval = flush_interval
        self._on_flush = on_flush
        self._process_lookup = ProcessLookup()
        self._stop = threading.Event()
        self._rdns_queue = []
        self._rdns_lock = threading.Lock()

    def _handle_packet(self, packet):
        proto = "tcp" if packet.tcp else ("udp" if packet.udp else None)
        if proto is None:
            return

        remote_port = packet.dst_port if packet.is_outbound else packet.src_port
        if remote_port in self._exclude_ports:
            return

        local_ip = packet.src_addr if packet.is_outbound else packet.dst_addr
        local_port = packet.src_port if packet.is_outbound else packet.dst_port
        remote_ip = packet.dst_addr if packet.is_outbound else packet.src_addr

        # Normalize the flow key regardless of which direction we happen to
        # observe first, so both directions of one connection share a bucket.
        flow_key = FlowKey(proto, local_ip, local_port, remote_ip, remote_port)

        domain = None
        payload = packet.tcp.payload if packet.tcp else (packet.udp.payload if packet.udp else b"")
        if packet.is_outbound and payload:
            if remote_port in _TLS_PORTS:
                domain = sni_parser.parse_tls_client_hello_sni(payload)
            elif remote_port in _HTTP_PORTS:
                domain = sni_parser.parse_http_host(payload)

        process_name = None
        if packet.is_outbound:
            process_name = self._process_lookup.get_process_name(proto, local_port)

        direction = "sent" if packet.is_outbound else "received"
        nbytes = len(packet.raw) if hasattr(packet, "raw") else packet.packet_len

        self._aggregator.on_packet(flow_key, direction, nbytes, domain=domain, process_name=process_name)

        if domain is None and not _is_private(remote_ip):
            with self._rdns_lock:
                self._rdns_queue.append(flow_key)

    def _rdns_worker(self):
        while not self._stop.is_set():
            with self._rdns_lock:
                batch, self._rdns_queue = self._rdns_queue, []
            for flow_key in batch:
                if flow_key not in self._aggregator.unresolved_flows():
                    continue
                name = reverse_dns(flow_key.remote_ip)
                self._aggregator.resolve_pending(flow_key, name)
            self._stop.wait(2.0)

    def _flush_loop(self):
        while not self._stop.wait(self._flush_interval):
            records = self._aggregator.flush()
            if records and self._on_flush:
                self._on_flush(records)

    def run(self):
        """Blocks until stop() is called from another thread."""
        wd_filter = "(tcp or udp) and !loopback"
        threading.Thread(target=self._rdns_worker, daemon=True).start()
        threading.Thread(target=self._flush_loop, daemon=True).start()

        with pydivert.WinDivert(wd_filter, layer=pydivert.Layer.NETWORK) as w:
            while not self._stop.is_set():
                try:
                    packet = w.recv(timeout=1000)
                except OSError:
                    continue  # timeout with no packet; loop and check _stop
                if packet is None:
                    continue
                try:
                    self._handle_packet(packet)
                finally:
                    w.send(packet)  # always re-inject; we never drop traffic

    def stop(self):
        self._stop.set()
