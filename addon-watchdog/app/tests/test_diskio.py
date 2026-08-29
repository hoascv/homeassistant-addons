"""Device I/O arithmetic, which is where this gets quietly wrong.

Every expected value below is worked out by hand in the test, not copied from a
run — a test that asserts whatever the code produced would have caught none of
the mistakes these cover.
"""
import os

import pytest

import diskio

# major minor name reads rd_merged rd_sectors rd_ms writes wr_merged wr_sectors
# wr_ms in_flight io_ticks weighted_ms
STATS_A = """\
 259       0 nvme0n1 1000 0 8000 500 2000 0 16000 1000 0 5000 1500
   7       0 loop0 5 0 40 1 0 0 0 0 0 1 1
 259       1 nvme0n1p1 10 0 80 5 20 0 160 10 0 50 15
"""

# 10 seconds later: +100 reads, +200 writes, +8000 read sectors (4 MB),
# +16000 write sectors (8 MB), +300ms read time, +2000ms write time,
# +5000ms io_ticks.
STATS_B = """\
 259       0 nvme0n1 1100 0 16000 800 2200 0 32000 3000 0 10000 1500
   7       0 loop0 5 0 40 1 0 0 0 0 0 1 1
 259       1 nvme0n1p1 10 0 80 5 20 0 160 10 0 50 15
"""


def _write(tmp_path, content):
    path = tmp_path / "diskstats"
    path.write_text(content)
    return str(path)


def test_virtual_devices_are_ignored(tmp_path):
    """loop and dm devices would double-count or mislead; only real storage."""
    devices = diskio.read_diskstats(_write(tmp_path, STATS_A))
    assert "nvme0n1" in devices
    assert "loop0" not in devices


def test_rates_are_computed_from_the_difference(tmp_path):
    a = diskio.read_diskstats(_write(tmp_path, STATS_A))["nvme0n1"]
    b = diskio.read_diskstats(_write(tmp_path, STATS_B))["nvme0n1"]
    s = diskio.sample(a, b, elapsed_seconds=10)

    assert s["read_iops"] == 10.0          # 100 reads / 10s
    assert s["write_iops"] == 20.0         # 200 writes / 10s
    assert s["iops"] == 30.0
    # 8000 sectors x 512 B = 4 MiB over 10s
    assert s["read_mb_s"] == pytest.approx(0.39, abs=0.01)
    assert s["write_mb_s"] == pytest.approx(0.78, abs=0.01)
    # io_ticks +5000ms over 10_000ms of wall clock
    assert s["util_percent"] == 50.0


def test_latency_is_per_operation_not_per_second():
    """The number that rises when the device is the bottleneck: 2000ms of write
    time spread over 200 writes is 10ms each, not 200ms/s of anything."""
    before = {"reads": 0, "writes": 0, "read_sectors": 0, "write_sectors": 0,
              "read_ms": 0, "write_ms": 0, "io_ticks": 0}
    after = {**before, "reads": 100, "writes": 200, "read_ms": 300, "write_ms": 2000}
    s = diskio.sample(before, after, elapsed_seconds=10)
    assert s["read_latency_ms"] == 3.0
    assert s["write_latency_ms"] == 10.0


def test_latency_is_none_when_nothing_happened():
    """No requests is not "instant" — reporting 0ms would flatten exactly the
    graph this exists to make readable."""
    zero = {"reads": 0, "writes": 0, "read_sectors": 0, "write_sectors": 0,
            "read_ms": 0, "write_ms": 0, "io_ticks": 0}
    s = diskio.sample(zero, dict(zero), elapsed_seconds=10)
    assert s["read_latency_ms"] is None and s["write_latency_ms"] is None
    assert s["iops"] == 0.0


def test_a_counter_reset_yields_no_sample():
    """A reboot, or the device being re-added, sends counters backwards. The
    difference is meaningless and a huge negative rate would read as a disaster
    rather than as bookkeeping."""
    high = {"reads": 1000, "writes": 1000, "read_sectors": 10, "write_sectors": 10,
            "read_ms": 10, "write_ms": 10, "io_ticks": 10}
    low = {key: 1 for key in high}
    assert diskio.sample(high, low, elapsed_seconds=10) is None


def test_zero_elapsed_time_yields_no_sample():
    """Two reads in the same instant would divide by zero."""
    counters = {"reads": 1, "writes": 1, "read_sectors": 1, "write_sectors": 1,
                "read_ms": 1, "write_ms": 1, "io_ticks": 1}
    assert diskio.sample(counters, counters, elapsed_seconds=0) is None


def test_utilisation_is_capped_at_100():
    """With several requests in flight io_ticks can drift past wall clock across
    a sampling boundary, and "103% busy" starts an argument about the instrument
    instead of the disk."""
    before = {"reads": 0, "writes": 0, "read_sectors": 0, "write_sectors": 0,
              "read_ms": 0, "write_ms": 0, "io_ticks": 0}
    after = {**before, "io_ticks": 11000}
    assert diskio.sample(before, after, elapsed_seconds=10)["util_percent"] == 100.0


# --- device identification ----------------------------------------------------


def test_the_data_device_is_found_by_major_minor(tmp_path, monkeypatch):
    """Matching on st_dev rather than guessing from names, which differ across
    sd/nvme/mmc hosts."""
    devices = diskio.read_diskstats(_write(tmp_path, STATS_A))

    class _Stat:
        st_dev = os.makedev(259, 0)

    monkeypatch.setattr(diskio.os, "stat", lambda path: _Stat())
    assert diskio.data_device(devices, "/data") == "nvme0n1"


def test_an_unmatched_device_falls_back_to_the_busiest(tmp_path, monkeypatch):
    """Better a named guess than nothing: the name is displayed, so a wrong one
    is visible rather than silent."""
    devices = diskio.read_diskstats(_write(tmp_path, STATS_A))

    class _Stat:
        st_dev = os.makedev(8, 99)

    monkeypatch.setattr(diskio.os, "stat", lambda path: _Stat())
    assert diskio.data_device(devices, "/data") == "nvme0n1"


def test_no_devices_means_no_answer():
    assert diskio.data_device({}, "/data") is None


# --- the sampler --------------------------------------------------------------


def test_the_first_poll_produces_nothing(tmp_path, monkeypatch):
    """Counters are cumulative; one reading is not a rate."""
    monkeypatch.setattr(diskio, "DISKSTATS", _write(tmp_path, STATS_A))
    sampler = diskio.Sampler()
    assert sampler.poll(now=1000) is None


def test_the_window_keeps_mean_and_peak(tmp_path, monkeypatch):
    """A 60-second mean hides a 10-second stall, which is the event being
    hunted, so both are published."""
    sampler = diskio.Sampler()
    sampler.device = "nvme0n1"
    sampler._window = [
        {"util_percent": 10.0, "iops": 100.0, "read_latency_ms": 1.0,
         "write_latency_ms": 2.0, "read_mb_s": 1.0, "write_mb_s": 1.0},
        {"util_percent": 100.0, "iops": 900.0, "read_latency_ms": 40.0,
         "write_latency_ms": 60.0, "read_mb_s": 1.0, "write_mb_s": 1.0},
    ]
    s = sampler.summary()
    assert s["util_percent"] == 55.0 and s["util_percent_peak"] == 100.0
    assert s["iops"] == 500.0 and s["iops_peak"] == 900.0
    assert s["write_latency_ms_peak"] == 60.0
    assert s["samples"] == 2
    assert sampler.summary() is None, "the window should reset after reporting"


def test_a_missing_diskstats_is_reported_not_crashed(tmp_path, monkeypatch):
    """If /proc/diskstats is masked in the container, that must surface as an
    error rather than as silence — the whole approach depends on it."""
    monkeypatch.setattr(diskio, "DISKSTATS", str(tmp_path / "absent"))
    sampler = diskio.Sampler()
    assert sampler.poll(now=1000) is None
    assert "no usable device rows" in sampler.error


# --- the ceiling --------------------------------------------------------------


def test_benchmark_refuses_when_the_disk_is_nearly_full(tmp_path, monkeypatch):
    """This disk has hit zero twice; a benchmark that fills it would be worse
    than no benchmark."""
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 100 * 1048576)
    result, err = diskio.run_benchmark(size_mb=1024, data_dir=str(tmp_path))
    assert result is None and "refusing to fill the disk" in err


def test_benchmark_reports_a_missing_fio_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 50 * 1073741824)
    monkeypatch.setattr(diskio.shutil, "which", lambda name: None)
    result, err = diskio.run_benchmark(data_dir=str(tmp_path))
    assert result is None and "fio is not installed" in err


def test_benchmark_removes_its_test_file_even_when_it_fails(tmp_path, monkeypatch):
    """A stale gigabyte in /data is the kind of leftover that fills a disk weeks
    later, so cleanup is in a finally rather than the happy path."""
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 50 * 1073741824)
    monkeypatch.setattr(diskio.shutil, "which", lambda name: "/usr/bin/fio")
    target = tmp_path / ".fio-benchmark.tmp"

    def _fake_run(cmd, **kwargs):
        target.write_text("scratch")
        raise OSError("device error")

    monkeypatch.setattr(diskio.subprocess, "run", _fake_run)
    result, err = diskio.run_benchmark(data_dir=str(tmp_path))
    assert result is None and err
    assert not target.exists(), "the test file outlived a failed run"


def test_saturation_is_measured_against_the_write_ceiling():
    """Random writes are the slower side on nearly every device, and writes are
    what the complaint is about."""
    summary = {"iops_peak": 450.0}
    benchmark = {"randread": {"iops": 5000.0}, "randwrite": {"iops": 900.0}}
    assert diskio.utilisation_against_ceiling(summary, benchmark) == 50.0


def test_no_ceiling_means_no_percentage():
    """Without a benchmark the utilisation figure still stands on its own; a
    made-up denominator would not."""
    assert diskio.utilisation_against_ceiling({"iops_peak": 100.0}, None) is None
    assert diskio.utilisation_against_ceiling(None, {"randwrite": {"iops": 1}}) is None


def test_an_unreadable_directory_is_a_refusal_not_a_traceback(tmp_path):
    """It runs on a background thread, so an exception here would surface only
    as stderr noise — the reader would see a button that did nothing."""
    data, err = diskio.run_benchmark(data_dir=str(tmp_path / "absent"))
    assert data is None
    assert "cannot check free space" in err


# --- the benchmark, when it works ---------------------------------------------


def _fio_output(iops, bw_kb, p95_ns, side="read"):
    """The shape fio actually emits with --output-format=json, trimmed to the
    fields read here."""
    import json as _json
    return _json.dumps({"jobs": [{side: {
        "iops": iops, "bw": bw_kb,
        "clat_ns": {"percentile": {"95.000000": p95_ns}},
    }}]})


def _fio(monkeypatch, tmp_path, outputs):
    """Stub fio itself, so the parsing and the file handling are what run."""
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 50 * 1073741824)
    monkeypatch.setattr(diskio.shutil, "which", lambda name: "/usr/bin/fio")
    monkeypatch.setattr(diskio, "BENCHMARK_FILE", str(tmp_path / "benchmark.json"))
    calls = []

    class _Proc:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Proc(outputs[len(calls) - 1])

    monkeypatch.setattr(diskio.subprocess, "run", fake_run)
    return calls


def test_a_benchmark_measures_both_directions_and_converts_the_units(tmp_path, monkeypatch):
    """fio reports bandwidth in KB/s and latency in nanoseconds; the page shows
    MB/s and milliseconds. Getting either conversion wrong is off by 1000."""
    _fio(monkeypatch, tmp_path, [
        _fio_output(5000.4, 40960, 1_500_000, side="read"),
        _fio_output(900.6, 8192, 12_000_000, side="write"),
    ])
    result, err = diskio.run_benchmark(size_mb=64, seconds=2, data_dir=str(tmp_path))

    assert err is None
    assert result["randread"] == {"iops": 5000.4, "mb_s": 40.0, "latency_ms_p95": 1.5}
    assert result["randwrite"] == {"iops": 900.6, "mb_s": 8.0, "latency_ms_p95": 12.0}
    assert result["block_size"] == "8k"
    assert "floor on the device" in result["note"]


def test_the_configured_size_and_runtime_reach_fio(tmp_path, monkeypatch):
    calls = _fio(monkeypatch, tmp_path, [
        _fio_output(1, 1, 1, side="read"), _fio_output(1, 1, 1, side="write"),
    ])
    diskio.run_benchmark(size_mb=256, seconds=15, data_dir=str(tmp_path))
    assert "--size=256m" in calls[0]
    assert "--runtime=15" in calls[0]
    assert "--rw=randread" in calls[0]
    assert "--rw=randwrite" in calls[1]


def test_a_benchmark_is_saved_so_it_survives_a_restart(tmp_path, monkeypatch):
    """The ceiling is measured on demand and compared against on every scan; a
    number that vanished on restart would make the saturation column blank until
    someone pressed the button again."""
    _fio(monkeypatch, tmp_path, [
        _fio_output(4000, 32768, 1_000_000, side="read"),
        _fio_output(800, 6144, 9_000_000, side="write"),
    ])
    written, _ = diskio.run_benchmark(data_dir=str(tmp_path))
    assert diskio.load_benchmark() == written


def test_a_failing_fio_reports_its_own_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 50 * 1073741824)
    monkeypatch.setattr(diskio.shutil, "which", lambda name: "/usr/bin/fio")

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "  no such device  "

    monkeypatch.setattr(diskio.subprocess, "run", lambda cmd, **kw: _Proc())
    result, err = diskio.run_benchmark(data_dir=str(tmp_path))
    assert result is None
    assert err == "fio randread failed: no such device"


def test_a_benchmark_that_hangs_is_given_up_on(tmp_path, monkeypatch):
    """--time_based bounds fio's own runtime, so a run that outlasts the timeout
    is wedged rather than slow, and waiting longer will not help."""
    monkeypatch.setattr(diskio, "_free_bytes", lambda path: 50 * 1073741824)
    monkeypatch.setattr(diskio.shutil, "which", lambda name: "/usr/bin/fio")

    def hang(cmd, **kwargs):
        raise diskio.subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(diskio.subprocess, "run", hang)
    result, err = diskio.run_benchmark(data_dir=str(tmp_path))
    assert result is None and err == "fio timed out"


def test_unrecognisable_fio_output_is_reported_not_raised(tmp_path, monkeypatch):
    """A different fio version reshaping its JSON should degrade to a message,
    not a traceback on a background thread."""
    _fio(monkeypatch, tmp_path, ['{"jobs": []}', '{"jobs": []}'])
    result, err = diskio.run_benchmark(data_dir=str(tmp_path))
    assert result is None
    assert err.startswith("fio produced no usable result:")


def test_a_benchmark_file_that_is_missing_or_corrupt_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(diskio, "BENCHMARK_FILE", str(tmp_path / "absent.json"))
    assert diskio.load_benchmark() is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{half written")
    monkeypatch.setattr(diskio, "BENCHMARK_FILE", str(corrupt))
    assert diskio.load_benchmark() is None


# --- diskstats the kernel would not normally emit -----------------------------


def test_a_truncated_row_is_skipped(tmp_path):
    """Older kernels emit fewer columns; taking parts[12] from one of those rows
    would be an IndexError on a background thread."""
    devices = diskio.read_diskstats(_write(tmp_path, " 259 0 nvme0n1 1000 0 8000\n"))
    assert devices == {}


def test_a_row_with_unparsable_numbers_is_skipped(tmp_path):
    stats = " 259       0 nvme0n1 x 0 8000 500 2000 0 16000 1000 0 5000 1500\n"
    assert diskio.read_diskstats(_write(tmp_path, stats)) == {}
