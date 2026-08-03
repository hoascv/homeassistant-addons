"""The merge itself, against a real Spark with Delta.

Marked `spark` because it needs a JVM. It is slow and it is the only thing that
exercises the MERGE — the idempotency guarantee the whole watermark design rests
on is a property of this code and nothing else checks it.
"""
import json

import pytest

pytestmark = pytest.mark.spark

# At module scope, so a machine without them skips this file at collection
# rather than erroring and taking the whole run down with it. `trackers_merge`
# imports both, so it cannot be imported at the top either.
pytest.importorskip("pyspark")
pytest.importorskip("delta")

from trackers_merge import merge_batch  # noqa: E402


def _write(prefix, name, payload):
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / name).write_text(json.dumps(payload, separators=(",", ":")))


def _export(rows, max_seq=10):
    return {
        "tables": {"workout_logs": rows},
        # The export names its key column: the JSON has its keys sorted, so the
        # id is rarely first, and guessing would silently merge unrelated rows.
        "keys": {"workout_logs": "id"},
        "max_seq": max_seq,
    }


def _change(row_id, seq, op="U", row=None, actor="user"):
    return {"table": "workout_logs", "row_id": str(row_id), "seq": seq, "op": op,
            "row": row, "changed_at": "2026-08-03T10:00:00", "actor": actor}


def _rows(spark, lake, table="workout_logs", source="gym_tracker"):
    df = spark.read.format("delta").load(f"{lake}/{source}/{table}")
    return {r["row_id"]: r for r in df.collect()}


def test_a_bootstrap_creates_a_table_per_source_table(spark, tmp_path):
    _write(tmp_path / "b1", "export.json",
           _export([{"id": 1, "reps": 10}, {"id": 2, "reps": 20}]))
    lake = str(tmp_path / "lake")

    written = merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), lake)

    assert written == [{"table": "workout_logs", "action": "created", "rows": 2}]
    rows = _rows(spark, lake)
    assert set(rows) == {"1", "2"}
    # The payload is kept as JSON, untouched — no schema was inferred.
    assert json.loads(rows["1"]["data"]) == {"id": 1, "reps": 10}
    assert rows["1"]["deleted_at"] is None


def test_an_update_replaces_the_row_and_a_delete_is_soft(spark, tmp_path):
    _write(tmp_path / "b1", "export.json", _export([{"id": 1, "reps": 10},
                                                    {"id": 2, "reps": 20}]))
    lake = str(tmp_path / "lake")
    merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), lake)

    _write(tmp_path / "b2", "changes-10.json", {"changes": [
        _change(1, 11, "U", {"id": 1, "reps": 99}),
        _change(2, 12, "D", None),
    ]})
    merge_batch(spark, "gym_tracker", str(tmp_path / "b2"), lake)

    rows = _rows(spark, lake)
    assert json.loads(rows["1"]["data"])["reps"] == 99
    # Deleted rows are kept: "logged then taken back" is worth analysing, and
    # these apps really do delete.
    assert rows["2"]["deleted_at"] is not None
    assert rows["2"]["data"] is None
    assert rows["2"]["actor"] == "user"


def test_replaying_a_batch_changes_nothing(spark, tmp_path):
    """The property the watermark design depends on: the watermark only advances
    after a successful merge, so a failure re-runs the same batch."""
    _write(tmp_path / "b1", "export.json", _export([{"id": 1, "reps": 10}]))
    lake = str(tmp_path / "lake")
    merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), lake)
    _write(tmp_path / "b2", "changes-10.json",
           {"changes": [_change(1, 11, "U", {"id": 1, "reps": 55})]})

    merge_batch(spark, "gym_tracker", str(tmp_path / "b2"), lake)
    once = _rows(spark, lake)
    merge_batch(spark, "gym_tracker", str(tmp_path / "b2"), lake)
    twice = _rows(spark, lake)

    assert len(once) == len(twice) == 1
    assert json.loads(twice["1"]["data"])["reps"] == 55


def test_an_older_change_cannot_undo_newer_state(spark, tmp_path):
    """Guarded by seq, so re-applying an old archive after a newer one has
    landed converges rather than regressing."""
    _write(tmp_path / "b1", "export.json", _export([{"id": 1, "reps": 10}]))
    lake = str(tmp_path / "lake")
    merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), lake)
    _write(tmp_path / "new", "changes-10.json",
           {"changes": [_change(1, 30, "U", {"id": 1, "reps": 300})]})
    merge_batch(spark, "gym_tracker", str(tmp_path / "new"), lake)

    _write(tmp_path / "old", "changes-10.json",
           {"changes": [_change(1, 20, "U", {"id": 1, "reps": 200})]})
    merge_batch(spark, "gym_tracker", str(tmp_path / "old"), lake)

    assert json.loads(_rows(spark, lake)["1"]["data"])["reps"] == 300


def test_several_changes_to_one_row_collapse_to_the_latest(spark, tmp_path):
    # MERGE requires at most one source row per key, so a batch touching the
    # same row twice must be reduced before it reaches Delta.
    _write(tmp_path / "b1", "changes-0.json", {"changes": [
        _change(1, 5, "I", {"id": 1, "reps": 1}),
        _change(1, 6, "U", {"id": 1, "reps": 2}),
        _change(1, 7, "U", {"id": 1, "reps": 3}),
    ]})
    lake = str(tmp_path / "lake")
    written = merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), lake)

    assert written[0]["rows"] == 1
    assert json.loads(_rows(spark, lake)["1"]["data"])["reps"] == 3


def test_an_export_without_keys_is_refused(spark, tmp_path):
    # Rather than merge every row of that table onto a null id.
    payload = _export([{"id": 1}])
    payload.pop("keys")
    _write(tmp_path / "b1", "export.json", payload)

    with pytest.raises(ValueError, match="did not name the key column"):
        merge_batch(spark, "gym_tracker", str(tmp_path / "b1"), str(tmp_path / "lake"))


def test_an_empty_batch_writes_nothing(spark, tmp_path):
    _write(tmp_path / "b1", "changes-0.json", {"changes": []})
    assert merge_batch(spark, "gym_tracker", str(tmp_path / "b1"),
                       str(tmp_path / "lake")) == []
