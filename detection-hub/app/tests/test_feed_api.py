"""The endpoints `pipeline-airflow` reads.

`jobs/trackers_feed.py` parses specific JSON paths out of these and fails in
unhelpful ways if they move, so the envelope is asserted here rather than left
to whatever jsonify produced on the day.
"""
import store


# --- recording ----------------------------------------------------------------


def test_detect_stores_nothing_without_a_camera(client, street_jpeg):
    """The try-it page posts images all day. None of that belongs in the
    database or in the lakehouse."""
    client.post("/api/detect", data=street_jpeg)
    assert client.get("/api/stats").get_json()["counts"]["detections"] == 0


def test_detect_with_a_camera_records_it(client, street_jpeg):
    """`?camera=` is what turns the API from a calculator into an input source
    on the same footing as an RTSP stream."""
    body = client.post("/api/detect?camera=front_door", data=street_jpeg).get_json()

    assert body["count"] > 0
    assert body["stored"]["camera"] == "front_door"
    stats = client.get("/api/stats").get_json()
    assert stats["counts"]["detections"] == body["count"]
    assert stats["counts"]["cameras"] == 1


def test_a_recorded_detection_keeps_its_snapshot(client, street_jpeg):
    body = client.post("/api/detect?camera=front_door", data=street_jpeg).get_json()
    snapshot_id = body["stored"]["snapshot_id"]
    assert snapshot_id

    res = client.get(f"/api/snapshots/{snapshot_id}")
    assert res.status_code == 200
    assert res.mimetype == "image/jpeg"
    assert res.data[:2] == b"\xff\xd8"


def test_a_missing_snapshot_is_a_404(client):
    assert client.get("/api/snapshots/9999").status_code == 404


def test_nothing_is_stored_when_nothing_was_detected(client):
    """An empty frame should not leave a camera row claiming a detection, nor a
    snapshot of nothing."""
    import io

    import cv2
    import numpy as np

    blank = np.full((200, 200, 3), 128, dtype=np.uint8)
    encoded = cv2.imencode(".jpg", blank)[1].tobytes()
    body = client.post("/api/detect?camera=empty", data=encoded).get_json()

    assert body["count"] == 0
    assert "stored" not in body
    assert client.get("/api/stats").get_json()["other_counts"]["snapshots"] == 0


# --- the feed envelope --------------------------------------------------------


def test_export_has_the_shape_the_pipeline_parses(client, street_jpeg):
    client.post("/api/detect?camera=front_door", data=street_jpeg)
    payload = client.get("/api/export").get_json()

    assert set(payload) >= {"app_version", "taken_at", "max_seq", "keys", "tables"}
    assert payload["keys"]["detections"] == "id"
    assert isinstance(payload["tables"]["detections"], list)
    assert payload["max_seq"] > 0


def test_changes_has_the_shape_the_pipeline_parses(client, street_jpeg):
    client.post("/api/detect?camera=front_door", data=street_jpeg)
    payload = client.get("/api/changes?since=0").get_json()

    assert set(payload) >= {
        "changes", "since", "min_seq", "max_seq", "full_reload_required"
    }
    first = payload["changes"][0]
    assert set(first) == {
        "seq", "table", "row_id", "op", "changed_at", "actor", "row"
    }
    assert first["op"] in {"I", "U", "D"}


def test_changes_rejects_a_non_numeric_watermark(client):
    """A 400 that says which parameter, rather than a 500 from int()."""
    res = client.get("/api/changes?since=banana")
    assert res.status_code == 400
    assert "since" in res.get_json()["error"]


def test_changes_rejects_a_non_numeric_limit(client):
    res = client.get("/api/changes?limit=lots")
    assert res.status_code == 400


def test_stats_answers_without_serialising_rows(client, street_jpeg):
    client.post("/api/detect?camera=front_door", data=street_jpeg)
    payload = client.get("/api/stats").get_json()

    assert set(payload) >= {
        "app_version", "taken_at", "db_bytes", "max_seq",
        "counts", "other_counts", "total", "other_total", "total_all",
    }
    # The whole point: the watchdog polls this every minute and must not get
    # the database back with it.
    assert "detections" not in str(payload["counts"].values())
    assert payload["db_bytes"] > 0


def test_the_watermark_advances_and_only_new_rows_come_back(client, street_jpeg):
    client.post("/api/detect?camera=a", data=street_jpeg)
    first = client.get("/api/changes?since=0").get_json()
    watermark = first["max_seq"]

    client.post("/api/detect?camera=b", data=street_jpeg)
    second = client.get(f"/api/changes?since={watermark}").get_json()

    assert second["changes"], "the second batch should not be empty"
    cameras = {c["row"]["camera"] for c in second["changes"] if c["table"] == "detections"}
    assert cameras == {"b"}


def test_an_image_never_reaches_the_feed(client, street_jpeg):
    """Snapshots live in their own untracked table precisely so this cannot
    happen; a megabyte of base64 per change would sink the ingest."""
    client.post("/api/detect?camera=front_door", data=street_jpeg)

    export_text = client.get("/api/export").get_data(as_text=True)
    changes_text = client.get("/api/changes").get_data(as_text=True)

    assert "snapshots" not in client.get("/api/export").get_json()["tables"]
    for text in (export_text, changes_text):
        assert "/9j/" not in text, "base64 JPEG in the feed"
        assert len(text) < 200_000, "feed is unexpectedly large"


# --- reading back -------------------------------------------------------------


def test_recent_detections_are_newest_first(client, street_jpeg):
    client.post("/api/detect?camera=a", data=street_jpeg)
    client.post("/api/detect?camera=b", data=street_jpeg)
    rows = client.get("/api/detections").get_json()["detections"]
    assert rows[0]["camera"] == "b"


def test_detections_can_be_filtered_by_camera(client, street_jpeg):
    client.post("/api/detect?camera=a", data=street_jpeg)
    client.post("/api/detect?camera=b", data=street_jpeg)
    rows = client.get("/api/detections?camera=a").get_json()["detections"]
    assert {r["camera"] for r in rows} == {"a"}


def test_detections_can_be_filtered_by_date_range(client, street_jpeg, db_path):
    """The calendar picker's backend. Rows are placed on known days directly,
    since the API always stamps 'now'."""
    import store

    client.post("/api/detect?camera=drive", data=street_jpeg)  # today, with a snapshot
    conn = store.connect(db_path, actor="user")
    for day in ("2026-01-10", "2026-02-15", "2026-03-20"):
        store.record_detections(conn, "drive",
                                [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                                at=f"{day}T12:00:00")
    conn.commit(); conn.close()

    got = client.get("/api/detections?from=2026-02-01&to=2026-02-28").get_json()["detections"]
    assert [d["detected_at"][:10] for d in got] == ["2026-02-15"]


def test_a_single_bound_works_alone(client, street_jpeg, db_path):
    import store
    conn = store.connect(db_path, actor="user")
    for day in ("2026-01-10", "2026-03-20"):
        store.record_detections(conn, "drive",
                                [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                                at=f"{day}T12:00:00")
    conn.commit(); conn.close()

    after = client.get("/api/detections?from=2026-02-01").get_json()["detections"]
    assert [d["detected_at"][:10] for d in after] == ["2026-03-20"]
    before = client.get("/api/detections?to=2026-02-01").get_json()["detections"]
    assert [d["detected_at"][:10] for d in before] == ["2026-01-10"]


def test_detections_can_be_filtered_by_time_within_a_day(client, db_path):
    """The point of adding time: pick a window inside a single day."""
    import store

    conn = store.connect(db_path, actor="user")
    for hhmm in ("08:00", "12:30", "18:45"):
        store.record_detections(conn, "drive",
                                [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                                at=f"2026-08-09T{hhmm}:00")
    conn.commit(); conn.close()

    got = client.get(
        "/api/detections?from=2026-08-09T12:00&to=2026-08-09T13:00"
    ).get_json()["detections"]
    assert [d["detected_at"] for d in got] == ["2026-08-09T12:30:00"]


def test_a_date_only_bound_still_covers_the_whole_day(client, db_path):
    """Backward compatible: a plain date must include midnight-to-midnight, not
    stop at 00:00:00 — which is what a naive `<=` comparison would do."""
    import store

    conn = store.connect(db_path, actor="user")
    for hhmm in ("00:00", "23:59"):
        store.record_detections(conn, "drive",
                                [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                                at=f"2026-08-09T{hhmm}:00")
    conn.commit(); conn.close()

    got = client.get("/api/detections?from=2026-08-09&to=2026-08-09").get_json()["detections"]
    assert len(got) == 2, "a date-only range dropped part of its own day"


def test_a_minute_bound_is_inclusive_of_that_minute(client, db_path):
    import store

    conn = store.connect(db_path, actor="user")
    store.record_detections(conn, "drive",
                            [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                            at="2026-08-09T14:30:45")
    conn.commit(); conn.close()

    got = client.get(
        "/api/detections?from=2026-08-09T14:30&to=2026-08-09T14:30"
    ).get_json()["detections"]
    assert len(got) == 1, "a :45 detection was excluded by a same-minute bound"


def test_a_malformed_date_is_a_400(client):
    res = client.get("/api/detections?from=last-tuesday")
    assert res.status_code == 400
    assert "from must be a date or date-time" in res.get_json()["error"]


def test_a_malformed_time_is_a_400(client):
    res = client.get("/api/detections?to=2026-08-09T25:99")
    assert res.status_code == 400


def test_no_date_is_the_live_view(client, street_jpeg):
    client.post("/api/detect?camera=drive", data=street_jpeg)
    assert client.get("/api/detections").get_json()["detections"]


def test_detections_can_be_filtered_by_object(client, db_path):
    import store

    conn = store.connect(db_path, actor="user")
    for label in ("car", "person", "car", "dog"):
        store.record_detections(conn, "drive",
                                [{"label": label, "confidence": 0.9, "box": [1, 2, 3, 4]}])
    conn.commit(); conn.close()

    got = client.get("/api/detections?label=car").get_json()["detections"]
    assert [d["label"] for d in got] == ["car", "car"]


def test_object_filter_accepts_several(client, db_path):
    import store

    conn = store.connect(db_path, actor="user")
    for label in ("car", "person", "dog"):
        store.record_detections(conn, "drive",
                                [{"label": label, "confidence": 0.9, "box": [1, 2, 3, 4]}])
    conn.commit(); conn.close()

    got = client.get("/api/detections?label=car,dog").get_json()["detections"]
    assert {d["label"] for d in got} == {"car", "dog"}


def test_an_empty_label_is_not_a_filter(client, street_jpeg):
    """A trailing comma or blank must mean "everything", not "nothing"."""
    client.post("/api/detect?camera=drive", data=street_jpeg)
    assert client.get("/api/detections?label=").get_json()["detections"]
    assert client.get("/api/detections?label=,").get_json()["detections"]


def test_object_and_date_filters_combine(client, db_path):
    import store

    conn = store.connect(db_path, actor="user")
    store.record_detections(conn, "drive",
                            [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                            at="2026-08-09T12:00:00")
    store.record_detections(conn, "drive",
                            [{"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                            at="2026-08-09T12:00:00")
    store.record_detections(conn, "drive",
                            [{"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                            at="2026-08-08T12:00:00")
    conn.commit(); conn.close()

    got = client.get(
        "/api/detections?label=car&from=2026-08-09&to=2026-08-09"
    ).get_json()["detections"]
    assert [(d["label"], d["detected_at"][:10]) for d in got] == [("car", "2026-08-09")]


def test_labels_endpoint_lists_what_was_seen_most_common_first(client, db_path):
    import store

    conn = store.connect(db_path, actor="user")
    for label in ("car", "car", "car", "person", "dog", "dog"):
        store.record_detections(conn, "drive",
                                [{"label": label, "confidence": 0.9, "box": [1, 2, 3, 4]}])
    conn.commit(); conn.close()

    labels = client.get("/api/labels").get_json()["labels"]
    assert labels[0] == "car"           # most frequent first
    assert set(labels) == {"car", "person", "dog"}


def test_labels_endpoint_is_empty_before_anything_is_seen(client):
    assert client.get("/api/labels").get_json()["labels"] == []


def test_labels_endpoint_needs_the_token_on_the_published_port(direct_client):
    assert direct_client.get("/api/labels").status_code == 401


def test_cameras_lists_what_has_been_seen(client, street_jpeg):
    client.post("/api/detect?camera=front_door", data=street_jpeg)
    cameras = client.get("/api/cameras").get_json()["cameras"]
    assert [c["id"] for c in cameras] == ["front_door"]
    assert cameras[0]["kind"] == "api"


# --- the gate covers all of it ------------------------------------------------


def test_every_feed_endpoint_requires_the_token_on_the_published_port(direct_client):
    """These are the bulk-read endpoints — the ones worth protecting."""
    for url in ("/api/export", "/api/changes", "/api/stats", "/api/detections",
                "/api/cameras", "/api/snapshots/1"):
        assert direct_client.get(url).status_code == 401, url


# --- retention ----------------------------------------------------------------


def test_retention_options_are_read_and_clamped(client, set_options):
    import app as hub

    set_options(detection_retention_days=5, snapshot_retention_days=2,
                snapshot_max_count=10)
    assert hub.get_retention() == {
        "detection_days": 5, "snapshot_days": 2, "snapshot_max": 10
    }

    set_options(detection_retention_days="lots")
    assert hub.get_retention()["detection_days"] == 30
