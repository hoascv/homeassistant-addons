"""Progress photos: the body over time.

Two things make these different from every other record in the app. They are
photographs of somebody, so they are reachable only through Home Assistant and
never over the published port — whose only credential is a token sitting in
another add-on's configuration. And they live in the database rather than as
files, which means a backup is also the album.
"""
import io
import struct
import zlib
from datetime import date, timedelta


def _png(width=8, height=8):
    """A real PNG, so the server's content sniffing sees what it expects."""
    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _upload(client, angle="front", taken_on=None, notes=None, data=None):
    payload = {"file": (io.BytesIO(data or _png()), "photo.png"), "angle": angle}
    if taken_on:
        payload["taken_on"] = taken_on
    if notes:
        payload["notes"] = notes
    return client.post("/api/progress-photos", data=payload,
                       content_type="multipart/form-data")


def _list(client):
    return client.get("/api/progress-photos").get_json()


# --- the thing that makes these different -----------------------------------


def test_the_published_port_refuses_them(direct_client):
    """A pipeline token reads weights and workouts. It is the wrong key for
    photographs of somebody's body, so the port refuses regardless."""
    for method, path in (
        ("get", "/api/progress-photos"),
        ("get", "/api/progress-photos/1/image"),
        ("delete", "/api/progress-photos/1"),
    ):
        res = getattr(direct_client, method)(path)
        assert res.status_code in (401, 403), f"{method} {path} answered {res.status_code}"


def test_the_published_port_refuses_an_upload(direct_client):
    res = direct_client.post("/api/progress-photos",
                             data={"file": (io.BytesIO(_png()), "photo.png")},
                             content_type="multipart/form-data")
    assert res.status_code in (401, 403)


def test_they_are_absent_from_the_change_feed(client, conn):
    """The feed is served over that same port. Even the metadata — that a photo
    was taken, and when — stays out of it."""
    import app as gymapp

    assert "progress_photos" not in gymapp.TRACKED_TABLES
    _upload(client)
    changed = {r["table_name"] for r in conn.execute("SELECT DISTINCT table_name FROM change_log")}
    assert "progress_photos" not in changed


# --- storing and reading -----------------------------------------------------


def test_a_photo_is_stored_and_comes_back(client):
    res = _upload(client, angle="side", notes="morning, fasted")

    assert res.status_code == 201, res.get_json()
    pid = res.get_json()["id"]
    photos = _list(client)["photos"]
    assert len(photos) == 1
    assert photos[0]["angle"] == "side"
    assert photos[0]["notes"] == "morning, fasted"
    assert photos[0]["taken_on"] == date.today().isoformat()

    img = client.get(f"/api/progress-photos/{pid}/image")
    assert img.status_code == 200
    assert img.mimetype == "image/png"
    assert img.data == _png()


def test_the_image_is_not_cached_publicly(client):
    """It is reached through a proxy, and must not sit in a shared cache."""
    pid = _upload(client).get_json()["id"]

    res = client.get(f"/api/progress-photos/{pid}/image")

    assert "private" in res.headers.get("Cache-Control", "")


def test_a_photo_carries_that_day_s_weigh_in(client, conn):
    """A photo next to the number is the comparison; a photo alone is a photo."""
    today = date.today().isoformat()
    logged = client.post("/api/weight",
                         json={"weight_kg": 100.7, "body_fat_pct": 27.9, "date": today})
    assert logged.status_code in (200, 201), logged.get_json()

    _upload(client, taken_on=today)

    photo = _list(client)["photos"][0]
    assert photo["weight_kg"] == 100.7
    assert photo["body_fat_pct"] == 27.9


def test_a_photo_with_no_weigh_in_says_so_rather_than_guessing(client):
    _upload(client)

    photo = _list(client)["photos"][0]
    assert photo["weight_kg"] is None
    assert photo["body_fat_pct"] is None


def test_photos_are_newest_first(client):
    for n in (10, 2, 30):
        _upload(client, taken_on=(date.today() - timedelta(days=n)).isoformat())

    days = [p["taken_on"] for p in _list(client)["photos"]]
    assert days == sorted(days, reverse=True)


def test_deleting_one_removes_it(client):
    pid = _upload(client).get_json()["id"]

    assert client.delete(f"/api/progress-photos/{pid}").status_code == 200
    assert _list(client)["photos"] == []
    assert client.get(f"/api/progress-photos/{pid}/image").status_code == 404


# --- what can actually be compared -------------------------------------------


def test_an_angle_with_one_photo_is_not_comparable(client):
    """Two photos make a comparison. One makes a photo."""
    _upload(client, angle="front")

    assert _list(client)["comparable_angles"] == []


def test_an_angle_with_two_photos_is(client):
    _upload(client, angle="front", taken_on=(date.today() - timedelta(days=30)).isoformat())
    _upload(client, angle="front")
    _upload(client, angle="back")

    body = _list(client)
    assert body["comparable_angles"] == ["front"], "a lone back photo is not a comparison"


# --- refusals ----------------------------------------------------------------


def test_a_file_that_is_not_an_image_is_refused(client):
    res = client.post("/api/progress-photos",
                      data={"file": (io.BytesIO(b"this is not a picture"), "x.png")},
                      content_type="multipart/form-data")
    assert res.status_code == 400
    assert "JPEG, PNG or WebP" in res.get_json()["error"]


def test_an_oversized_photo_is_refused_and_says_why(client):
    import app as gymapp

    big = _png() + b"\x00" * (gymapp.MAX_PHOTO_BYTES + 1)
    res = client.post("/api/progress-photos",
                      data={"file": (io.BytesIO(big), "big.png")},
                      content_type="multipart/form-data")
    assert res.status_code == 400
    # The reason matters: it is about the backup, not an arbitrary limit.
    assert "backup" in res.get_json()["error"]


def test_an_unknown_angle_is_refused(client):
    res = _upload(client, angle="sideways")
    assert res.status_code == 400
    assert "front, side, back" in res.get_json()["error"]


def test_a_future_date_is_refused(client):
    res = _upload(client, taken_on=(date.today() + timedelta(days=1)).isoformat())
    assert res.status_code == 400
    assert "future" in res.get_json()["error"]


def test_a_malformed_date_is_refused(client):
    res = _upload(client, taken_on="last tuesday")
    assert res.status_code == 400
    assert "YYYY-MM-DD" in res.get_json()["error"]


def test_no_file_is_refused(client):
    res = client.post("/api/progress-photos", data={"angle": "front"},
                      content_type="multipart/form-data")
    assert res.status_code == 400


def test_a_missing_photo_is_a_404(client):
    assert client.get("/api/progress-photos/9999/image").status_code == 404
    assert client.delete("/api/progress-photos/9999").status_code == 404
