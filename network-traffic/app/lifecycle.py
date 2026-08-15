"""Turning rotated pcap files into MinIO objects, and keeping the local
buffer bounded while it does.

Runs as its own loop, polling /data/pcap on a short interval rather than
reacting to tcpdump directly — the two processes do not need to coordinate,
and a poll is simple enough to be obviously correct.
"""
from __future__ import annotations

import os
import re
import shutil
import time

import capture
import extract
import uploader

CAPTURE_NAME_RE = re.compile(r"^capture-\d{8}T\d{6}Z\.pcap$")

# How long a file must sit unmodified before it is treated as a completed
# rotation. Well under any sane rotate_seconds, and only there to cover the
# instant right at rotation where tcpdump has closed one file and not yet
# started writing to the directory listing's idea of the next.
ACTIVE_GUARD_SECONDS = 2


def list_capture_files(pcap_dir=None):
    """Every rotated pcap tcpdump has written, oldest first.

    tcpdump's filename template is a strftime stamp, so lexicographic order
    is chronological order — no need to stat anything to know which file
    came first.
    """
    directory = pcap_dir or capture.PCAP_DIR
    try:
        names = sorted(name for name in os.listdir(directory) if CAPTURE_NAME_RE.match(name))
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names]


def completed_files(paths, now=None, guard_seconds=ACTIVE_GUARD_SECONDS):
    """Every file except the one tcpdump is still writing to.

    That is always the last one in `paths` (see list_capture_files), so
    completion needs no more than dropping it — the mtime guard on what is
    left is defensive, in case a rotation was caught mid-swap and the
    "last" file is not actually the active one.
    """
    if len(paths) < 2:
        return []
    now = now if now is not None else time.time()
    result = []
    for path in paths[:-1]:
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age >= guard_seconds:
            result.append(path)
    return result


def jsonl_path_for(pcap_path):
    return pcap_path[:-len(".pcap")] + ".jsonl"


class Lifecycle:
    """Extract -> upload -> delete for each completed rotation, plus the
    retention backstop tcpdump's own -G-only invocation does not provide.
    """

    def __init__(self, options, log=print):
        self.options = options
        self.log = log
        self.client = None
        self.label = uploader.resolve_label(options.get("capture_label"))
        self.uploaded_count = 0
        self.discarded_count = 0
        self.last_rotation_at = None
        self.last_upload_at = None
        self.last_bytes = None
        self.last_error = None
        self._stop = False

    def _ensure_client(self):
        if self.client is not None:
            return self.client
        client = uploader.make_client(
            self.options["minio_endpoint"],
            self.options["minio_access_key"],
            self.options["minio_secret_key"],
        )
        err = uploader.ensure_bucket(client, self.options["minio_bucket"])
        if err:
            self.last_error = f"MinIO bucket: {err}"
            self.log(self.last_error)
            return None

        err = uploader.ensure_lifecycle(
            client, self.options["minio_bucket"], self.options["minio_prefix"],
            self.options.get("datalake_retention_days", 7),
        )
        if err:
            # Not fatal: uploads can proceed without an expiry rule in place —
            # losing automatic cleanup costs storage, not correctness, and
            # isn't worth blocking on.
            self.log(f"MinIO lifecycle rule: {err}")

        self.client = client
        return client

    def datalake_usage(self):
        """(count, total_bytes, error) for this add-on's own prefix in
        MinIO — a lazy, on-demand call rather than part of the regular poll
        loop, since listing every object is a real round trip not worth
        paying on a 5-second cycle nobody is looking at.
        """
        client = self._ensure_client()
        if client is None:
            return None, None, self.last_error or "MinIO client unavailable"
        return uploader.count_prefix(client, self.options["minio_bucket"], self.options["minio_prefix"])

    def clear_datalake(self):
        """Permanently delete everything this add-on has uploaded under its
        own prefix. Never touches the local pending backlog — those files
        are legitimately not-yet-shipped captures, and will just upload
        again normally on the next pass.
        """
        client = self._ensure_client()
        if client is None:
            return None, None, self.last_error or "MinIO client unavailable"
        count, freed_bytes, err = uploader.clear_prefix(
            client, self.options["minio_bucket"], self.options["minio_prefix"],
        )
        if err:
            self.log(f"clear datalake: {count} deleted before error: {err}")
        else:
            self.log(
                f"cleared datalake: {count} objects, {freed_bytes / 1048576:.1f} MB "
                f"deleted from {self.options['minio_prefix']}/"
            )
        return count, freed_bytes, err

    def _enforce_retention(self, paths):
        """Delete the oldest local files, unconditionally, once the backlog
        exceeds retention_files. This is the disk-safety net that leaving
        tcpdump's own -W flag unused deliberately gives up — a MinIO outage
        longer than the retention window loses the oldest data rather than
        filling the disk.
        """
        overflow = len(paths) - self.options["retention_files"]
        if overflow <= 0:
            return
        for pcap_path in paths[:overflow]:
            for path in (pcap_path, jsonl_path_for(pcap_path)):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.discarded_count += 1
            self.log(
                f"buffer overrun — discarded unshipped capture "
                f"{os.path.basename(pcap_path)} (MinIO unreachable or falling behind)"
            )

    def process_once(self):
        all_files = list_capture_files()
        self._enforce_retention(all_files)
        ready = completed_files(list_capture_files())

        for pcap_path in ready:
            self.last_rotation_at = time.time()
            try:
                self.last_bytes = os.path.getsize(pcap_path)
            except OSError:
                self.last_bytes = None

            jsonl_path = jsonl_path_for(pcap_path)
            count, err = extract.extract_jsonl(pcap_path, jsonl_path)
            if err:
                self.last_error = f"extract {os.path.basename(pcap_path)}: {err}"
                self.log(self.last_error)
                continue  # left in place; retried next pass

            client = self._ensure_client()
            if client is None:
                continue  # error already logged by _ensure_client

            keys, err = uploader.upload_pair(
                client, self.options["minio_bucket"], pcap_path, jsonl_path,
                self.options["minio_prefix"], self.label,
            )
            if err:
                self.last_error = f"upload {os.path.basename(pcap_path)}: {err}"
                self.log(self.last_error)
                continue

            for path in (pcap_path, jsonl_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.uploaded_count += 1
            self.last_upload_at = time.time()
            self.last_error = None
            self.log(f"uploaded {keys[0]} ({count} packets)")

    def run_forever(self, poll_seconds=5):
        while not self._stop:
            try:
                self.process_once()
            except Exception as exc:  # noqa: BLE001 - the loop outlives one pass
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.log(f"lifecycle pass raised {self.last_error}")
            time.sleep(poll_seconds)

    def stop(self):
        self._stop = True

    def status(self):
        pending = len(list_capture_files())
        throughput = None
        rotate_seconds = self.options.get("rotate_seconds")
        if self.last_bytes is not None and rotate_seconds:
            throughput = round(self.last_bytes / rotate_seconds, 1)
        disk_free_mb = None
        try:
            disk_free_mb = round(shutil.disk_usage(capture.PCAP_DIR).free / 1048576, 1)
        except OSError:
            pass
        return {
            "pending_files": pending,
            "uploaded_count": self.uploaded_count,
            "discarded_count": self.discarded_count,
            "last_rotation_at": self.last_rotation_at,
            "last_upload_at": self.last_upload_at,
            "last_error": self.last_error,
            "throughput_bytes_per_sec": throughput,
            "disk_free_mb": disk_free_mb,
        }
