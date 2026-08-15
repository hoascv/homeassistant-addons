"""capture.py: the tcpdump argv is a pure function, and the supervisor loop is
exercised entirely against a fake subprocess — no real tcpdump involved, the
same style diskio.py's fio tests use for run_benchmark.
"""
import os

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
    assert status["paused"] is False
    assert status["pid"] is None
    assert status["restarts"] == 0


def test_init_starts_paused_if_flag_file_already_exists(monkeypatch, tmp_path):
    flag_path = tmp_path / "paused"
    flag_path.write_text("")
    monkeypatch.setattr(capture, "PAUSE_FLAG_PATH", str(flag_path))

    cap = capture.Capture({}, log=lambda *_: None)

    assert cap.paused is True


def test_pause_sets_flag_and_terminates_a_running_process(monkeypatch, tmp_path):
    flag_path = str(tmp_path / "paused")
    monkeypatch.setattr(capture, "PAUSE_FLAG_PATH", flag_path)

    cap = capture.Capture({}, log=lambda *_: None)
    cap.process = FakeProcess()

    cap.pause()

    assert cap.paused is True
    assert os.path.exists(flag_path)
    assert cap.process.terminated is True


def test_resume_clears_the_flag(monkeypatch, tmp_path):
    flag_path = str(tmp_path / "paused")
    monkeypatch.setattr(capture, "PAUSE_FLAG_PATH", flag_path)

    cap = capture.Capture({}, log=lambda *_: None)
    cap.pause()
    assert os.path.exists(flag_path)

    cap.resume()

    assert cap.paused is False
    assert not os.path.exists(flag_path)


def test_run_forever_holds_without_spawning_while_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "PAUSE_FLAG_PATH", str(tmp_path / "paused"))
    monkeypatch.setattr(capture.os, "makedirs", lambda *a, **k: None)

    processes = []
    monkeypatch.setattr(capture.subprocess, "Popen", lambda cmd, **k: processes.append(FakeProcess()))

    cap = capture.Capture(
        {"capture_interfaces": "any", "bpf_filter": "", "rotate_seconds": 60, "snap_length": 0},
        log=lambda *_: None,
    )
    cap.pause()

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        cap.stop()

    monkeypatch.setattr(capture.time, "sleep", fake_sleep)

    cap.run_forever()

    assert processes == []  # tcpdump never spawned while paused
    assert sleeps == [1]  # the paused holding-pattern sleep, not a backoff sleep


def test_exit_while_paused_is_not_counted_as_a_restart(monkeypatch, tmp_path):
    """Simulates pause() terminating the process while run_forever's wait()
    was blocked on it — by the time wait() returns, self.paused is already
    true, and that exit must not be treated as a crash.
    """
    monkeypatch.setattr(capture, "PAUSE_FLAG_PATH", str(tmp_path / "paused"))
    monkeypatch.setattr(capture.os, "makedirs", lambda *a, **k: None)

    cap = capture.Capture(
        {"capture_interfaces": "any", "bpf_filter": "", "rotate_seconds": 60, "snap_length": 0},
        log=lambda *_: None,
    )

    class PausedMidWaitProcess(FakeProcess):
        def wait(self):
            cap.paused = True  # what pause() would have set, from another thread
            self._returncode = -15
            return -15

    monkeypatch.setattr(capture.subprocess, "Popen", lambda cmd, **k: PausedMidWaitProcess())

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        cap.stop()

    monkeypatch.setattr(capture.time, "sleep", fake_sleep)

    cap.run_forever()

    assert cap.restarts == 0
    assert sleeps == [1]  # only the paused holding-pattern sleep, no backoff
