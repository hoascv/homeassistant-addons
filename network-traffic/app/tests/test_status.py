"""status.py: the watchdog status file, temp-file-then-rename, same shape as
detection-hub's write_status.
"""
import json
import os

import status


def test_write_status_lands_a_readable_json_file(tmp_path):
    err = status.write_status(True, "capturing", metrics={"pending_files": 2}, status_dir=str(tmp_path))
    assert err is None

    target = tmp_path / "network-traffic.json"
    assert target.exists()
    body = json.loads(target.read_text())
    assert body["slug"] == "network-traffic"
    assert body["ok"] is True
    assert body["detail"] == "capturing"
    assert body["metrics"] == {"pending_files": 2}
    assert isinstance(body["updated_at"], int)
    # No leftover temp file after the rename.
    assert not (tmp_path / "network-traffic.json.tmp").exists()


def test_write_status_never_raises_on_an_unwritable_directory(tmp_path):
    unwritable = tmp_path / "no-such-parent" / "deep" / "path"
    # A file in place of what should be a directory forces makedirs to fail.
    (tmp_path / "no-such-parent").write_bytes(b"not a directory")
    err = status.write_status(False, "tcpdump is not running", status_dir=str(unwritable))
    assert err is not None
    assert isinstance(err, str)
