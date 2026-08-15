"""lifecycle.py: file-selection helpers are pure and tested directly; the
extract/upload calls Lifecycle makes are monkeypatched out entirely, so
nothing here touches a real tshark, MinIO, or pcap.
"""
import os
import time

import capture
import extract
import lifecycle
import uploader


def _touch(path, mtime):
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


def test_list_capture_files_sorts_chronologically_and_filters(tmp_path):
    (tmp_path / "capture-20260815T120500Z.pcap").write_bytes(b"x")
    (tmp_path / "capture-20260815T120000Z.pcap").write_bytes(b"x")
    (tmp_path / "not-a-capture.pcap").write_bytes(b"x")

    files = lifecycle.list_capture_files(str(tmp_path))
    assert [os.path.basename(f) for f in files] == [
        "capture-20260815T120000Z.pcap",
        "capture-20260815T120500Z.pcap",
    ]


def test_completed_files_needs_at_least_two():
    assert lifecycle.completed_files([]) == []
    assert lifecycle.completed_files(["/only/one.pcap"]) == []


def test_completed_files_drops_only_the_newest(tmp_path):
    now = 1_000_000.0
    old = tmp_path / "capture-20260815T000000Z.pcap"
    mid = tmp_path / "capture-20260815T000100Z.pcap"
    newest = tmp_path / "capture-20260815T000200Z.pcap"
    _touch(old, now - 100)
    _touch(mid, now - 50)
    _touch(newest, now - 100)  # mtime is irrelevant for the newest — position decides

    paths = [str(old), str(mid), str(newest)]
    assert lifecycle.completed_files(paths, now=now, guard_seconds=2) == [str(old), str(mid)]


def test_completed_files_guards_a_just_written_older_file(tmp_path):
    now = 1_000_000.0
    old = tmp_path / "capture-20260815T000000Z.pcap"
    recent = tmp_path / "capture-20260815T000100Z.pcap"
    newest = tmp_path / "capture-20260815T000200Z.pcap"
    _touch(old, now - 100)
    _touch(recent, now - 1)  # inside the guard window
    _touch(newest, now)

    paths = [str(old), str(recent), str(newest)]
    assert lifecycle.completed_files(paths, now=now, guard_seconds=2) == [str(old)]


def test_lifecycle_uploads_completed_rotation_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))

    now = time.time()
    done = tmp_path / "capture-20260815T000000Z.pcap"
    active = tmp_path / "capture-20260815T000100Z.pcap"
    _touch(done, now - 100)
    _touch(active, now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda pcap_path, jsonl_path, timeout=120: (5, None))
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)

    uploaded = []

    def fake_upload_pair(client, bucket, pcap_path, jsonl_path, prefix, label):
        uploaded.append(pcap_path)
        return ("network_traffic/x.pcap", "network_traffic/x.jsonl"), None

    monkeypatch.setattr(uploader, "upload_pair", fake_upload_pair)

    options = {
        "retention_files": 10, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
    }
    life = lifecycle.Lifecycle(options, log=lambda *_: None)
    life.process_once()

    assert life.uploaded_count == 1
    assert uploaded == [str(done)]
    assert not done.exists()  # deleted after a successful upload
    assert active.exists()  # still the active file, left alone


def test_lifecycle_leaves_files_in_place_when_extract_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))

    now = time.time()
    done = tmp_path / "capture-20260815T000000Z.pcap"
    active = tmp_path / "capture-20260815T000100Z.pcap"
    _touch(done, now - 100)
    _touch(active, now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda *a, **k: (0, "tshark crashed"))

    options = {
        "retention_files": 10, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
    }
    life = lifecycle.Lifecycle(options, log=lambda *_: None)
    life.process_once()

    assert life.uploaded_count == 0
    assert "tshark crashed" in life.last_error
    assert done.exists()  # left for a retry on the next pass


def test_lifecycle_enforces_retention_backstop(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))

    now = time.time()
    paths = []
    for i in range(4):
        p = tmp_path / f"capture-20260815T00000{i}Z.pcap"
        _touch(p, now - (100 - i))
        paths.append(p)

    # extract never succeeds, so nothing would be removed except via retention.
    monkeypatch.setattr(extract, "extract_jsonl", lambda *a, **k: (0, "boom"))

    options = {
        "retention_files": 2, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
    }
    life = lifecycle.Lifecycle(options, log=lambda *_: None)
    life.process_once()

    remaining = lifecycle.list_capture_files(str(tmp_path))
    assert len(remaining) == 2
    assert life.discarded_count == 2
    # The two oldest are gone, the two newest survive.
    assert os.path.basename(remaining[0]) == "capture-20260815T000002Z.pcap"
