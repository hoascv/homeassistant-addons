"""capture.py: the tcpdump argv is a pure function, and the supervisor loop is
exercised entirely against a fake subprocess — no real tcpdump involved, the
same style diskio.py's fio tests use for run_benchmark.
"""
import capture


def test_build_command_includes_required_flags(tmp_path):
    cmd = capture.build_command("any", "", 300, 0, pcap_dir=str(tmp_path))
    assert cmd[0] == "tcpdump"
    assert cmd[cmd.index("-i") + 1] == "any"
    assert cmd[cmd.index("-s") + 1] == "0"
    assert cmd[cmd.index("-G") + 1] == "300"
    # -Z root: without it tcpdump drops to `nobody` after the first file and
    # every later rotation fails with EACCES — see the comment in capture.py.
    assert cmd[cmd.index("-Z") + 1] == "root"
    assert str(tmp_path) in cmd[cmd.index("-w") + 1]


def test_build_command_splits_bpf_filter(tmp_path):
    cmd = capture.build_command("eth0", "not port 22", 60, 0, pcap_dir=str(tmp_path))
    assert cmd[-3:] == ["not", "port", "22"]


def test_build_command_omits_filter_when_blank(tmp_path):
    cmd = capture.build_command("eth0", "", 60, 0, pcap_dir=str(tmp_path))
    assert cmd[-1] == "-U"


class FakeProcess:
    """Exits the instant .wait() is called, with a fixed stderr line — enough
    to drive the restart loop without a real tcpdump anywhere near it."""

    def __init__(self, returncode=1, stderr_lines=("permission denied",)):
        self.pid = 4242
        self.stderr = iter(f"{line}\n" for line in stderr_lines)
        self.terminated = False
        self._returncode = None
        self._final_returncode = returncode

    def wait(self):
        self._returncode = self._final_returncode
        return self._final_returncode

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = -15


def test_capture_restarts_with_backoff_and_reports_last_error(monkeypatch):
    processes = []
    sleeps = []

    def fake_popen(cmd, **kwargs):
        proc = FakeProcess()
        processes.append(proc)
        return proc

    cap = capture.Capture(
        {"capture_interfaces": "any", "bpf_filter": "", "rotate_seconds": 60, "snap_length": 0},
        log=lambda *_: None,
    )

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            cap.stop()

    monkeypatch.setattr(capture.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(capture.time, "sleep", fake_sleep)
    monkeypatch.setattr(capture.os, "makedirs", lambda *a, **k: None)

    # Deterministic and thread-free: fake_sleep stops the loop itself once it
    # has seen three restarts, so run_forever returns on its own.
    cap.run_forever()

    assert cap.restarts == 3
    assert len(processes) == 3
    assert cap.last_error == "permission denied"
    # Backoff doubles each time a run ends quickly: 1, 2, 4.
    assert sleeps == [1, 2, 4]


def test_status_reports_not_running_before_first_spawn():
    cap = capture.Capture({}, log=lambda *_: None)
    status = cap.status()
    assert status["running"] is False
    assert status["pid"] is None
    assert status["restarts"] == 0
