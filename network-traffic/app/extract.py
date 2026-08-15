"""Turning a completed pcap into the JSONL sidecar that is actually queried.

tshark rather than a hand-rolled parser (scapy/pyshark): it is a mature,
already-correct dissector for exactly the fields wanted here — DNS query
names, TLS SNI, plaintext HTTP host/path — and shelling out to it is far less
code than reimplementing protocol parsing. `-T fields` rather than `-T json`/
`-T ek`: the flat schema below is already known in full, so one row per
packet with a trivial split() beats a per-protocol nested shape that varies
with which dissectors fired on a given packet.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

# One row per packet, in this order. `_ws.col.Protocol`/`_ws.col.Info` are
# tshark's own human-readable summary columns (e.g. "TLSv1.3" / "Client Hello",
# "DNS" / "Standard query A example.com") — the field that most directly
# answers "what data was exchanged" for plaintext protocols, and it costs
# nothing extra once tshark is already being asked to parse the file.
FIELDS = [
    "frame.time_epoch",
    "ip.src", "ip.dst", "ipv6.src", "ipv6.dst",
    "ip.proto", "frame.len",
    "tcp.srcport", "tcp.dstport", "udp.srcport", "udp.dstport",
    "dns.qry.name", "tls.handshake.extensions_server_name",
    "http.host", "http.request.uri",
    "_ws.col.Protocol", "_ws.col.Info",
]


def build_command(pcap_path):
    """The tshark argv, as a pure function — so a test can check exactly what
    would run without a real pcap or tshark itself.

    `-n` disables tshark's own reverse-DNS resolution: without it, parsing a
    file full of DNS traffic would have tshark issuing DNS lookups of its own
    while it reads — slow, and a subtle correctness trap (it would generate
    exactly the kind of traffic under investigation). `-E occurrence=f` keeps
    each field to its first value, so a packet with more than one match for a
    field (rare, but real for some layered protocols) still produces one flat
    row rather than silently shifting every column after it.
    """
    cmd = [
        "tshark", "-r", pcap_path, "-n",
        "-T", "fields", "-E", "header=n", "-E", "separator=\t", "-E", "occurrence=f",
    ]
    for field in FIELDS:
        cmd += ["-e", field]
    return cmd


def record_from_fields(values):
    """One tab-split tshark line, reduced to the shape a reader actually
    wants — a single src/dst pair regardless of IPv4 vs IPv6, a single port
    pair regardless of TCP vs UDP, and empty strings turned into absence
    rather than carried as noise. Pure and tshark-free, so it is testable on
    its own.
    """
    raw = dict(zip(FIELDS, values))

    def get(key):
        return raw.get(key) or None

    def as_float(key):
        value = get(key)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def as_int(key):
        value = get(key)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    return {
        "time": as_float("frame.time_epoch"),
        "src_ip": get("ip.src") or get("ipv6.src"),
        "dst_ip": get("ip.dst") or get("ipv6.dst"),
        "ip_proto": get("ip.proto"),
        "length": as_int("frame.len"),
        "src_port": as_int("tcp.srcport") or as_int("udp.srcport"),
        "dst_port": as_int("tcp.dstport") or as_int("udp.dstport"),
        "dns_query": get("dns.qry.name"),
        "tls_sni": get("tls.handshake.extensions_server_name"),
        "http_host": get("http.host"),
        "http_uri": get("http.request.uri"),
        "protocol": get("_ws.col.Protocol"),
        "info": get("_ws.col.Info"),
    }


def extract_jsonl(pcap_path, jsonl_path, timeout=120):
    """Run tshark over one completed pcap, writing one JSON record per packet
    to jsonl_path. Returns (record_count, error) — never raises, since this
    runs inside the lifecycle loop and a bad pcap must not take it down.
    """
    if not shutil.which("tshark"):
        return 0, "tshark is not installed in this image"

    try:
        proc = subprocess.run(
            build_command(pcap_path), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 0, f"tshark timed out after {timeout}s"
    except OSError as exc:
        return 0, f"could not run tshark: {exc}"
    if proc.returncode != 0:
        return 0, f"tshark failed: {proc.stderr.strip()[:200]}"

    count = 0
    tmp_path = f"{jsonl_path}.tmp"
    try:
        with open(tmp_path, "w") as handle:
            for line in proc.stdout.splitlines():
                if not line:
                    continue
                record = record_from_fields(line.split("\t"))
                handle.write(json.dumps(record) + "\n")
                count += 1
        os.replace(tmp_path, jsonl_path)
    except OSError as exc:
        return count, f"could not write {jsonl_path}: {exc}"
    return count, None
