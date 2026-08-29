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
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)

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


def test_lifecycle_upload_proceeds_even_if_the_retention_rule_fails(tmp_path, monkeypatch):
    """A failed lifecycle-rule call costs automatic cleanup, not correctness
    — it must not block the upload it has nothing to do with."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))

    now = time.time()
    done = tmp_path / "capture-20260815T000000Z.pcap"
    active = tmp_path / "capture-20260815T000100Z.pcap"
    _touch(done, now - 100)
    _touch(active, now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda pcap_path, jsonl_path, timeout=120: (5, None))
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: "MinIO does not support ILM")
    monkeypatch.setattr(
        uploader, "upload_pair",
        lambda client, bucket, pcap_path, jsonl_path, prefix, label: (("x.pcap", "x.jsonl"), None),
    )

    options = {
        "retention_files": 10, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
        "datalake_retention_days": 7,
    }
    life = lifecycle.Lifecycle(options, log=lambda *_: None)
    life.process_once()

    assert life.uploaded_count == 1
    assert life.client is not None


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


def _options():
    return {
        "retention_files": 10, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
        "datalake_retention_days": 7,
    }


def test_datalake_usage_delegates_to_uploader_count_prefix(monkeypatch):
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)
    monkeypatch.setattr(uploader, "count_prefix", lambda client, bucket, prefix: (3, 512, None))

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    count, total_bytes, err = life.datalake_usage()

    assert (count, total_bytes, err) == (3, 512, None)


def test_datalake_usage_reports_error_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: "bucket is on fire")

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    count, total_bytes, err = life.datalake_usage()

    assert count is None
    assert total_bytes is None
    assert err


def test_clear_datalake_delegates_to_uploader_clear_prefix(monkeypatch):
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)

    calls = []

    def fake_clear_prefix(client, bucket, prefix):
        calls.append((bucket, prefix))
        return 4, 4096, None

    monkeypatch.setattr(uploader, "clear_prefix", fake_clear_prefix)

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    count, freed_bytes, err = life.clear_datalake()

    assert (count, freed_bytes, err) == (4, 4096, None)
    assert calls == [("raw", "network_traffic")]  # never the wrong prefix


# --- the client, and what happens when MinIO will not have it -----------------


def _options(**overrides):
    base = {
        "retention_files": 10, "rotate_seconds": 300,
        "minio_endpoint": "http://x", "minio_access_key": "a", "minio_secret_key": "b",
        "minio_bucket": "raw", "minio_prefix": "network_traffic", "capture_label": "host",
        "datalake_retention_days": 7,
    }
    base.update(overrides)
    return base


def test_the_client_is_built_once_and_reused(monkeypatch):
    """process_once runs every five seconds; rebuilding a boto3 client each
    pass would be a TCP handshake a second for nothing."""
    built = []
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: built.append(1) or object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    first = life._ensure_client()
    assert life._ensure_client() is first
    assert len(built) == 1


def test_an_unreachable_bucket_yields_no_client_and_records_why(monkeypatch):
    """Uploading without a bucket cannot work, so this one *is* fatal to the
    pass — unlike the expiry rule below."""
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: "connection refused")

    logged = []
    life = lifecycle.Lifecycle(_options(), log=logged.append)
    assert life._ensure_client() is None
    assert "connection refused" in life.last_error
    assert logged


def test_a_failed_expiry_rule_is_logged_but_not_fatal(monkeypatch):
    """Losing automatic cleanup costs storage, not correctness — a MinIO whose
    user cannot set lifecycle rules must still be able to receive captures."""
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: "client")
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: "access denied")

    logged = []
    life = lifecycle.Lifecycle(_options(), log=logged.append)
    assert life._ensure_client() == "client"
    assert life.last_error is None, "an expiry-rule failure is not the add-on's error state"
    assert any("access denied" in line for line in logged)


def test_an_upload_failure_leaves_the_files_for_the_next_pass(tmp_path, monkeypatch):
    """Deleting on a failed upload would lose the capture outright; the backlog
    is the retry queue."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    now = time.time()
    done = tmp_path / "capture-20260815T000000Z.pcap"
    active = tmp_path / "capture-20260815T000100Z.pcap"
    _touch(done, now - 100)
    _touch(active, now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda p, j, timeout=120: (5, None))
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)
    monkeypatch.setattr(uploader, "upload_pair", lambda *a, **k: (None, "503 from MinIO"))

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    life.process_once()

    assert life.uploaded_count == 0
    assert done.exists(), "a failed upload must not delete the capture"
    assert "503 from MinIO" in life.last_error


def test_a_pass_that_cannot_reach_minio_at_all_keeps_the_backlog(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    now = time.time()
    done = tmp_path / "capture-20260815T000000Z.pcap"
    _touch(done, now - 100)
    _touch(tmp_path / "capture-20260815T000100Z.pcap", now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda p, j, timeout=120: (5, None))
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: "down")

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    life.process_once()
    assert life.uploaded_count == 0
    assert done.exists()


def test_a_successful_upload_clears_a_previous_error(tmp_path, monkeypatch):
    """Otherwise the status file reports a fault that recovered minutes ago and
    the watchdog sensor stays degraded forever."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    now = time.time()
    _touch(tmp_path / "capture-20260815T000000Z.pcap", now - 100)
    _touch(tmp_path / "capture-20260815T000100Z.pcap", now)

    monkeypatch.setattr(extract, "extract_jsonl", lambda p, j, timeout=120: (5, None))
    monkeypatch.setattr(uploader, "make_client", lambda *a, **k: object())
    monkeypatch.setattr(uploader, "ensure_bucket", lambda client, bucket: None)
    monkeypatch.setattr(uploader, "ensure_lifecycle", lambda *a, **k: None)
    monkeypatch.setattr(uploader, "upload_pair", lambda *a, **k: (("a.pcap", "a.jsonl"), None))

    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    life.last_error = "an earlier failure"
    life.process_once()
    assert life.last_error is None


# --- the loop -----------------------------------------------------------------


class _StopLoop(Exception):
    pass


def test_the_loop_outlives_a_pass_that_raises(monkeypatch):
    """One unreadable file must not end the shipping thread for good."""
    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)

    def boom():
        raise RuntimeError("disk vanished")

    def fake_sleep(_seconds):
        raise _StopLoop

    monkeypatch.setattr(life, "process_once", boom)
    monkeypatch.setattr(lifecycle.time, "sleep", fake_sleep)
    import pytest

    with pytest.raises(_StopLoop):
        life.run_forever(poll_seconds=1)
    assert "RuntimeError: disk vanished" in life.last_error


def test_stop_ends_the_loop(monkeypatch):
    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    life.stop()
    monkeypatch.setattr(life, "process_once",
                        lambda: (_ for _ in ()).throw(AssertionError("ran after stop")))
    life.run_forever(poll_seconds=1)  # returns immediately


# --- what the status file is built from ---------------------------------------


def test_status_reports_the_pending_backlog(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    for name in ("capture-20260815T000000Z.pcap", "capture-20260815T000100Z.pcap"):
        _touch(tmp_path / name, time.time())
    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    assert life.status()["pending_files"] == 2


def test_status_derives_throughput_from_the_last_rotation(tmp_path, monkeypatch):
    """Bytes per second across the rotation window — the number that says
    whether the link is busier than the uploader can keep up with."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    life = lifecycle.Lifecycle(_options(rotate_seconds=300), log=lambda *_: None)
    life.last_bytes = 3000
    assert life.status()["throughput_bytes_per_sec"] == 10.0


def test_status_has_no_throughput_before_the_first_rotation(tmp_path, monkeypatch):
    """Zero would read as an idle link rather than as "not measured yet"."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    assert life.status()["throughput_bytes_per_sec"] is None


def test_status_survives_an_unreadable_capture_directory(monkeypatch):
    """The status thread must keep reporting even when /share is the problem."""
    monkeypatch.setattr(capture, "PCAP_DIR", "/nonexistent/pcap")
    life = lifecycle.Lifecycle(_options(), log=lambda *_: None)
    body = life.status()
    assert body["pending_files"] == 0
    assert body["disk_free_mb"] is None


def test_listing_an_unreadable_directory_is_empty_not_an_exception(monkeypatch):
    monkeypatch.setattr(capture, "PCAP_DIR", "/nonexistent/pcap")
    assert lifecycle.list_capture_files() == []


def test_a_file_that_vanished_mid_pass_is_skipped(tmp_path, monkeypatch):
    """Retention can delete a file between listing it and stat-ing it."""
    monkeypatch.setattr(capture, "PCAP_DIR", str(tmp_path))
    paths = [str(tmp_path / "capture-20260815T000000Z.pcap"),
             str(tmp_path / "capture-20260815T000100Z.pcap")]
    assert lifecycle.completed_files(paths, now=time.time()) == []
