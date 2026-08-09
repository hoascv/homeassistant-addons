"""Device I/O, from /proc/diskstats — and how much of the disk is left.

The question this answers is not "how fast is the disk" but "was the disk the
constraint at 10am". Those need different evidence. A maximum says what the
device could do; utilisation says you are *at* it, and latency says what that
cost. The argument that holds up is the three together:

    utilisation pinned near 100%, average wait up from 2ms to 40ms, while the
    pipeline's own I/O was unchanged — so the load came from somewhere else.

/proc/diskstats is host-wide and not namespaced, so an unprivileged container
sees the real device counters. Everything here is cumulative-since-boot, so a
sample is only meaningful as a difference between two readings.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

DISKSTATS = os.environ.get("DISKSTATS_PATH", "/proc/diskstats")
DATA_DIR = os.environ.get("WATCHDOG_DATA_DIR", "/data")
BENCHMARK_FILE = os.path.join(DATA_DIR, "benchmark.json")

# A sector is 512 bytes in /proc/diskstats regardless of the device's real
# sector size — the kernel normalises it, and reading the field as anything
# else is a classic way to be wrong by 8x.
SECTOR_BYTES = 512

# Partitions and virtual devices would double-count against their parent, and
# loop/ram devices are not the storage anyone means.
_IGNORED = re.compile(r"^(loop|ram|zram|dm-|sr)\d*$")


def read_diskstats(path=None):
    """Raw cumulative counters per device, as a dict keyed by device name.

    Field numbers are the kernel's documented 1-based ones, minus the three
    leading columns (major, minor, name).
    """
    try:
        with open(path or DISKSTATS) as handle:
            lines = handle.readlines()
    except OSError:
        return {}

    devices = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if _IGNORED.match(name):
            continue
        try:
            devices[name] = {
                "major": int(parts[0]),
                "minor": int(parts[1]),
                "reads": int(parts[3]),
                "read_sectors": int(parts[5]),
                "read_ms": int(parts[6]),
                "writes": int(parts[7]),
                "write_sectors": int(parts[9]),
                "write_ms": int(parts[10]),
                # Milliseconds during which *any* request was in flight. This is
                # the utilisation numerator, and it is not the sum of read_ms and
                # write_ms: overlapping requests count once here and separately
                # there, which is why a busy queue can show read_ms far above
                # wall-clock time while io_ticks cannot exceed it.
                "io_ticks": int(parts[12]),
            }
        except ValueError:
            continue
    return devices


def sample(previous, current, elapsed_seconds):
    """One device's rates, from two cumulative readings.

    Returns None when the difference cannot be trusted: no elapsed time, or a
    counter that went backwards. The latter means a reboot or the device being
    re-added, and reporting a huge negative rate would look like a catastrophic
    event rather than a bookkeeping reset.
    """
    if not previous or not current or elapsed_seconds <= 0:
        return None

    d = {key: current[key] - previous[key]
         for key in ("reads", "writes", "read_sectors", "write_sectors",
                     "read_ms", "write_ms", "io_ticks")}
    if any(value < 0 for value in d.values()):
        return None

    elapsed_ms = elapsed_seconds * 1000.0
    return {
        # Capped at 100: with several requests in flight io_ticks can drift a
        # little past wall-clock across a sampling boundary, and "103% busy"
        # invites an argument about the instrument instead of the disk.
        "util_percent": min(100.0, round(d["io_ticks"] / elapsed_ms * 100, 1)),
        "read_iops": round(d["reads"] / elapsed_seconds, 1),
        "write_iops": round(d["writes"] / elapsed_seconds, 1),
        "iops": round((d["reads"] + d["writes"]) / elapsed_seconds, 1),
        "read_mb_s": round(d["read_sectors"] * SECTOR_BYTES / elapsed_seconds / 1048576, 2),
        "write_mb_s": round(d["write_sectors"] * SECTOR_BYTES / elapsed_seconds / 1048576, 2),
        # Average time a request waited, which is the number that rises when the
        # device is the bottleneck. Undefined rather than zero when nothing
        # happened — no requests is not "instant".
        "read_latency_ms": round(d["read_ms"] / d["reads"], 1) if d["reads"] else None,
        "write_latency_ms": round(d["write_ms"] / d["writes"], 1) if d["writes"] else None,
    }


def data_device(devices, path=None):
    """The device backing /data, or None.

    st_dev carries the major:minor of the filesystem the path lives on, which is
    what /proc/diskstats is keyed by underneath its device names. Matching on
    that avoids guessing from names, which differ across sd/nvme/mmc hosts.
    """
    try:
        st = os.stat(path or DATA_DIR)
    except OSError:
        return None
    major, minor = os.major(st.st_dev), os.minor(st.st_dev)
    for name, dev in devices.items():
        if dev["major"] == major and dev["minor"] == minor:
            return name
    # A partition's stats live under its own name while st_dev may point at the
    # whole-disk device, or the other way about on some setups. Falling back to
    # the busiest real device beats reporting nothing, and the name is shown so
    # a wrong guess is visible rather than silent.
    if devices:
        return max(devices, key=lambda n: devices[n]["io_ticks"])
    return None


class Sampler:
    """Rolling device statistics, sampled far more often than the main scan.

    Reading one file is cheap and the main scan takes ~13s, so this runs on its
    own short interval. Both the mean and the peak of each window are kept: a
    60-second mean hides a 10-second stall, and the stall is the event being
    hunted.
    """

    def __init__(self, interval=10):
        self.interval = interval
        self._previous = {}
        self._previous_at = None
        self._window = []
        self.device = None
        self.error = None

    def poll(self, now=None):
        now = now or time.time()
        devices = read_diskstats()
        if not devices:
            self.error = f"no usable device rows in {DISKSTATS}"
            return None
        self.error = None
        self.device = self.device or data_device(devices)

        result = None
        if self.device in devices and self.device in self._previous:
            result = sample(self._previous[self.device], devices[self.device],
                            now - self._previous_at)
            if result:
                self._window.append(result)
        self._previous = devices
        self._previous_at = now
        return result

    def summary(self, reset=True):
        """Mean and peak over the window, and then start a new one."""
        window, self._window = self._window, ([] if reset else self._window)
        if not window:
            return None

        def mean(key):
            values = [s[key] for s in window if s.get(key) is not None]
            return round(sum(values) / len(values), 1) if values else None

        def peak(key):
            values = [s[key] for s in window if s.get(key) is not None]
            return max(values) if values else None

        return {
            "device": self.device,
            "samples": len(window),
            "util_percent": mean("util_percent"),
            "util_percent_peak": peak("util_percent"),
            "iops": mean("iops"),
            "iops_peak": peak("iops"),
            "read_mb_s": mean("read_mb_s"),
            "write_mb_s": mean("write_mb_s"),
            "read_latency_ms": mean("read_latency_ms"),
            "read_latency_ms_peak": peak("read_latency_ms"),
            "write_latency_ms": mean("write_latency_ms"),
            "write_latency_ms_peak": peak("write_latency_ms"),
        }


# --- the ceiling --------------------------------------------------------------


def load_benchmark():
    try:
        with open(BENCHMARK_FILE) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _free_bytes(path):
    return shutil.disk_usage(path).free


def run_benchmark(size_mb=1024, seconds=30, min_free_gb=2, data_dir=None):
    """8 KiB random read and write IOPS, measured against the real device.

    8 KiB because that is Postgres' page size, which makes the result directly
    comparable to database I/O instead of a sequential MB/s figure nothing here
    is limited by. direct=1 bypasses the page cache — without it this measures
    RAM and reports a number ten times too high.

    Deliberately never scheduled. It writes real data and competes with the live
    database, so it is a decision, not a background task.
    """
    data_dir = data_dir or DATA_DIR
    target = os.path.join(data_dir, ".fio-benchmark.tmp")

    free = _free_bytes(data_dir)
    needed = max(size_mb * 1048576, min_free_gb * 1073741824)
    if free < needed:
        return None, (f"needs {needed // 1048576} MB free in {data_dir}, "
                      f"has {free // 1048576} MB — refusing to fill the disk")
    if not shutil.which("fio"):
        return None, "fio is not installed in this image"

    results = {}
    try:
        for mode in ("randread", "randwrite"):
            proc = subprocess.run(
                ["fio", "--name=bench", f"--filename={target}", f"--rw={mode}",
                 "--bs=8k", "--direct=1", "--ioengine=libaio", "--iodepth=16",
                 f"--size={size_mb}m", f"--runtime={seconds}", "--time_based",
                 "--output-format=json"],
                capture_output=True, text=True, timeout=seconds * 4,
            )
            if proc.returncode != 0:
                return None, f"fio {mode} failed: {proc.stderr.strip()[:200]}"
            job = json.loads(proc.stdout)["jobs"][0]
            side = job["read"] if mode == "randread" else job["write"]
            results[mode] = {
                "iops": round(side["iops"], 1),
                "mb_s": round(side["bw"] / 1024, 2),
                "latency_ms_p95": round(
                    side.get("clat_ns", {}).get("percentile", {})
                        .get("95.000000", 0) / 1_000_000, 2),
            }
    except subprocess.TimeoutExpired:
        return None, "fio timed out"
    except (OSError, ValueError, KeyError) as exc:
        return None, f"fio produced no usable result: {exc}"
    finally:
        # Always, including on failure: a stale gigabyte in /data is exactly the
        # kind of leftover that fills a disk weeks later.
        try:
            os.remove(target)
        except OSError:
            pass

    benchmark = {
        "measured_at": time.time(),
        "block_size": "8k",
        "randread": results["randread"],
        "randwrite": results["randwrite"],
        # Recorded because it is the caveat that makes the number honest: taken
        # while everything else was running, so it is a floor on the true
        # ceiling, not the ceiling itself.
        "note": "measured under live load; a floor on the device's true maximum",
    }
    try:
        with open(BENCHMARK_FILE, "w") as handle:
            json.dump(benchmark, handle)
    except OSError:
        pass
    return benchmark, None


def utilisation_against_ceiling(summary, benchmark):
    """Observed IOPS as a percentage of the measured ceiling, or None.

    Compared against the *write* ceiling: random writes are the slower of the
    two on nearly every device, and the complaint here is about writes.
    """
    if not summary or not benchmark or not summary.get("iops_peak"):
        return None
    ceiling = (benchmark.get("randwrite") or {}).get("iops")
    if not ceiling:
        return None
    return round(summary["iops_peak"] / ceiling * 100, 1)
