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
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
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
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", lambda *a, **k: fake)

    count, err = extract.extract_jsonl(str(tmp_path / "in.pcap"), str(tmp_path / "out.jsonl"))
    assert count == 0
    assert "not a pcap file" in err


# --- tshark refusing to cooperate ---------------------------------------------


def test_a_hanging_tshark_is_given_up_on(tmp_path, monkeypatch):
    """A malformed pcap can wedge tshark. The lifecycle loop calls this every
    five seconds, so a hang here would stop shipping entirely."""
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 120))

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", hang)
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(tmp_path / "a.jsonl"))
    assert count == 0
    assert "timed out" in err


def test_a_missing_tshark_says_so(tmp_path, monkeypatch):
    def missing(cmd, **kwargs):
        raise OSError("No such file or directory: tshark")

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", missing)
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(tmp_path / "a.jsonl"))
    assert count == 0
    assert "could not run tshark" in err


def test_a_failing_tshark_reports_its_own_stderr(tmp_path, monkeypatch):
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "  cut short in the middle of a packet  "

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", lambda cmd, **kw: _Proc())
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(tmp_path / "a.jsonl"))
    assert count == 0
    assert err == "tshark failed: cut short in the middle of a packet"


def test_an_unwritable_destination_is_reported_not_raised(tmp_path, monkeypatch):
    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", lambda cmd, **kw: _Proc())
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), "/nonexistent/dir/a.jsonl")
    assert count == 0
    assert "could not write" in err


def test_the_jsonl_is_written_atomically(tmp_path, monkeypatch):
    """Via a .tmp and os.replace, so the lifecycle loop can never pick up a
    half-written file and ship it as a complete rotation."""
    class _Proc:
        returncode = 0
        stdout = "1.5\t10.0.0.1\t10.0.0.2\t6\n"
        stderr = ""

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", lambda cmd, **kw: _Proc())
    target = tmp_path / "a.jsonl"
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(target))

    assert err is None
    assert count == 1
    assert target.exists()
    assert not (tmp_path / "a.jsonl.tmp").exists(), "the scratch file was left behind"


def test_blank_lines_from_tshark_are_not_records(tmp_path, monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "1.5\t10.0.0.1\t10.0.0.2\t6\n\n\n"
        stderr = ""

    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/tshark")
    monkeypatch.setattr(extract.subprocess, "run", lambda cmd, **kw: _Proc())
    count, _ = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(tmp_path / "a.jsonl"))
    assert count == 1


# --- fields tshark did not fill in --------------------------------------------


def test_an_unparsable_number_becomes_none_not_a_crash():
    """tshark emits whatever the packet carried; a malformed field must not take
    the whole rotation down."""
    record = extract.record_from_fields(["not-a-time", "10.0.0.1", "10.0.0.2", "6"])
    assert record["time"] is None
    assert record["src_ip"] == "10.0.0.1"


def test_empty_fields_become_none_not_empty_strings():
    """An empty string would be a value in the lakehouse; None is absence, and
    the two answer a "how many packets had no SNI" query differently."""
    record = extract.record_from_fields(["", "", "", ""])
    assert record["time"] is None
    assert record["src_ip"] is None


def test_a_missing_tshark_binary_is_reported_before_anything_runs(tmp_path, monkeypatch):
    """The minimal image would produce this on every rotation; saying which
    thing is absent beats a generic exec failure."""
    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    count, err = extract.extract_jsonl(str(tmp_path / "a.pcap"), str(tmp_path / "a.jsonl"))
    assert count == 0
    assert err == "tshark is not installed in this image"
