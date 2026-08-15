"""extract.py: field-mapping is a pure function tested directly, and the
tshark invocation is tested against a monkeypatched subprocess.run — no real
tshark or pcap involved.
"""
import json
import subprocess

import extract


def test_build_command_shape(tmp_path):
    pcap = str(tmp_path / "in.pcap")
    cmd = extract.build_command(pcap)
    assert cmd[:3] == ["tshark", "-r", pcap]
    assert "-n" in cmd
    for field in extract.FIELDS:
        assert field in cmd


def _fields(**overrides):
    """A row of blank fields with a few filled in, in FIELDS order."""
    values = {field: "" for field in extract.FIELDS}
    values.update(overrides)
    return [values[field] for field in extract.FIELDS]


def test_record_from_fields_prefers_ipv4_and_tcp():
    record = extract.record_from_fields(_fields(**{
        "frame.time_epoch": "1699999999.123456",
        "ip.src": "1.2.3.4",
        "ip.dst": "5.6.7.8",
        "ip.proto": "6",
        "frame.len": "1400",
        "tcp.srcport": "51820",
        "tcp.dstport": "443",
        "tls.handshake.extensions_server_name": "example.com",
        "_ws.col.Protocol": "TLSv1.3",
        "_ws.col.Info": "Client Hello",
    }))
    assert record["time"] == 1699999999.123456
    assert record["src_ip"] == "1.2.3.4"
    assert record["dst_ip"] == "5.6.7.8"
    assert record["length"] == 1400
    assert record["src_port"] == 51820
    assert record["dst_port"] == 443
    assert record["tls_sni"] == "example.com"
    assert record["dns_query"] is None
    assert record["protocol"] == "TLSv1.3"
    assert record["info"] == "Client Hello"


def test_record_from_fields_falls_back_to_ipv6_and_udp():
    record = extract.record_from_fields(_fields(**{
        "ipv6.src": "::1",
        "ipv6.dst": "::2",
        "udp.srcport": "53",
        "udp.dstport": "5353",
        "dns.qry.name": "example.com",
        "_ws.col.Protocol": "DNS",
    }))
    assert record["src_ip"] == "::1"
    assert record["dst_ip"] == "::2"
    assert record["src_port"] == 53
    assert record["dst_port"] == 5353
    assert record["dns_query"] == "example.com"


def test_extract_jsonl_reports_missing_tshark(monkeypatch, tmp_path):
    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    count, err = extract.extract_jsonl(str(tmp_path / "in.pcap"), str(tmp_path / "out.jsonl"))
    assert count == 0
    assert "not installed" in err


def test_extract_jsonl_writes_one_record_per_line(monkeypatch, tmp_path):
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    line = "\t".join(_fields(**{
        "frame.time_epoch": "1699999999.0",
        "ip.src": "1.2.3.4",
        "ip.dst": "5.6.7.8",
        "tcp.srcport": "51820",
        "tcp.dstport": "443",
        "tls.handshake.extensions_server_name": "example.com",
        "_ws.col.Protocol": "TLSv1.3",
        "_ws.col.Info": "Application Data",
    }))
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=line + "\n", stderr="")
    monkeypatch.setattr(extract.subprocess, "run", lambda *a, **k: fake)

    jsonl_path = tmp_path / "out.jsonl"
    count, err = extract.extract_jsonl(str(tmp_path / "in.pcap"), str(jsonl_path))

    assert err is None
    assert count == 1
    record = json.loads(jsonl_path.read_text().strip())
    assert record["src_ip"] == "1.2.3.4"
    assert record["tls_sni"] == "example.com"


def test_extract_jsonl_reports_tshark_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not a pcap file")
    monkeypatch.setattr(extract.subprocess, "run", lambda *a, **k: fake)

    count, err = extract.extract_jsonl(str(tmp_path / "in.pcap"), str(tmp_path / "out.jsonl"))
    assert count == 0
    assert "not a pcap file" in err
