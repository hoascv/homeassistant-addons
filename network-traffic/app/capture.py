"""Running tcpdump as a supervised subprocess.

tcpdump does the actual capture; this only keeps it alive and rotating. The
rotated files themselves are picked up and shipped by app.py's separate
lifecycle loop — this module knows nothing about MinIO or JSONL, only about
keeping one process running against /data/pcap.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time

PCAP_DIR = os.environ.get("PCAP_DIR", "/data/pcap")

# tcpdump's own strftime template for -w, expanded once per rotation. The
# ISO-8601-ish stamp sorts lexicographically the same as chronologically,
# which is what lets app.py's lifecycle loop tell a completed rotation from
# the one still being written without asking tcpdump anything.
FILENAME_TEMPLATE = "capture-%Y%m%dT%H%M%SZ.pcap"

# A run shorter than this on exit is treated as a crash loop and backs off;
# longer than this and the next restart goes back to the short delay. Chosen
# well under the smallest sane rotate_seconds, so a single successful rotation
# is enough evidence that tcpdump is actually working again.
BACKOFF_RESET_SECONDS = 60
MAX_BACKOFF_SECONDS = 60


def build_command(interfaces, bpf_filter, rotate_seconds, snap_length, pcap_dir=None):
    """The tcpdump argv, as a pure function — so a test can check exactly what
    would run without running tcpdump at all.
    """
    cmd = [
        "tcpdump",
        "-i", interfaces,
        "-s", str(snap_length),
        "-w", os.path.join(pcap_dir or PCAP_DIR, FILENAME_TEMPLATE),
        "-G", str(rotate_seconds),
        # tcpdump drops root privileges to `nobody` once the capture socket is
        # open, unless told not to — which would let the FIRST rotated file
        # succeed (opened before the drop) and every file after it fail with
        # EACCES, since nothing in this image gives `nobody` write access to
        # /data/pcap. This is exactly the kind of failure that would not show
        # up until the second rotation. The container already runs as root
        # with NET_ADMIN/NET_RAW granted directly by Supervisor's `privileged`
        # list, so staying root here gives up nothing.
        "-Z", "root",
        # Flush on file close rather than on every packet, which -U without a
        # count would otherwise cost on a busy interface for no benefit here.
        "-U",
    ]
    if bpf_filter:
        cmd += shlex.split(bpf_filter)
    return cmd


class Capture:
    """A tcpdump subprocess, restarted with backoff if it ever exits.

    No `-W` ring buffer: combined with `-G`, some tcpdump builds stop
    capturing entirely once the file-count limit is reached rather than
    wrapping — which would end all capture days before anyone noticed. Local
    retention is instead enforced by app.py's own janitor loop against the
    files this produces, which is deterministic and does not depend on which
    tcpdump build ships in the base image.
    """

    def __init__(self, options, log=print):
        self.options = options
        self.log = log
        self.process = None
        self.pid = None
        self.started_at = None
        self.restarts = 0
        self.last_error = None
        self._stop = False

    def _spawn(self):
        os.makedirs(PCAP_DIR, exist_ok=True)
        cmd = build_command(
            self.options["capture_interfaces"],
            self.options["bpf_filter"],
            self.options["rotate_seconds"],
            self.options["snap_length"],
        )
        self.log(f"starting: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        self.pid = self.process.pid
        self.started_at = time.time()

    def _drain_stderr(self):
        """tcpdump's stderr, forwarded line-by-line into this add-on's own
        log — otherwise a permission or interface error would be visible only
        as a silent, endlessly restarting process. Runs until the pipe closes,
        which happens when tcpdump exits.
        """
        if not self.process or not self.process.stderr:
            return
        for line in self.process.stderr:
            line = line.strip()
            if line:
                self.last_error = line
                self.log(f"tcpdump: {line}")

    def run_forever(self):
        """Start, wait, restart with backoff — forever, until stop() is
        called. Meant to run on its own daemon thread for the add-on's
        lifetime.
        """
        backoff = 1
        while not self._stop:
            self._spawn()
            self._drain_stderr()
            returncode = self.process.wait()
            if self._stop:
                return
            ran_for = time.time() - self.started_at
            self.restarts += 1
            self.log(
                f"tcpdump exited (code {returncode}) after {ran_for:.0f}s; "
                f"restart #{self.restarts} in {backoff}s"
            )
            time.sleep(backoff)
            backoff = 1 if ran_for > BACKOFF_RESET_SECONDS else min(backoff * 2, MAX_BACKOFF_SECONDS)

    def stop(self):
        self._stop = True
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def status(self):
        running = self.process is not None and self.process.poll() is None
        return {
            "running": running,
            "pid": self.pid if running else None,
            "started_at": self.started_at if running else None,
            "restarts": self.restarts,
            "last_error": self.last_error,
        }
