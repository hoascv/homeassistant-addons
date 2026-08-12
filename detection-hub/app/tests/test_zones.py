"""Zones: which part of the frame an object has to be in before it counts.

The polygon in most of these is the unit square's left half — x from 0 to 0.5 —
so every expected answer can be checked by looking at the coordinate rather than
by running the code.
"""
import zones

LEFT_HALF = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]

# Roughly a driveway: a wedge along the bottom-left of the frame, with the street
# above and to the right of it.
DRIVEWAY = [(0.05, 1.0), (0.35, 0.55), (0.75, 0.6), (0.95, 1.0)]


# --- the geometry -------------------------------------------------------------


def test_a_point_inside_and_outside_a_square():
    assert zones.contains(LEFT_HALF, 0.25, 0.5) is True
    assert zones.contains(LEFT_HALF, 0.75, 0.5) is False


def test_a_point_above_and_below_a_square():
    assert zones.contains([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)], 0.5, 0.1) is False
    assert zones.contains([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)], 0.5, 0.9) is False


def test_a_concave_shape_does_not_swallow_its_notch():
    """Ray casting earns its keep here: a driveway with a flower bed cut out of
    it is concave, and a bounding-box test would call the notch inside."""
    c_shape = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.6, 1.0),
               (0.6, 0.4), (0.4, 0.4), (0.4, 1.0), (0.0, 1.0)]
    assert zones.contains(c_shape, 0.5, 0.2) is True, "the top bar of the C"
    assert zones.contains(c_shape, 0.5, 0.8) is False, "the notch is outside"


# --- which point of a box decides ---------------------------------------------


def test_a_box_is_judged_where_it_meets_the_ground():
    """The whole reason bottom-centre rather than centre: a van across the road
    has a box whose middle floats over the driveway while its wheels are plainly
    in the street."""
    frame_w, frame_h = 1000, 1000
    # A tall box whose centre is at y=0.5 but whose base is at y=0.9.
    box = [400, 100, 200, 800]
    assert zones.ground_point(box, frame_w, frame_h) == (0.5, 0.9)


def test_a_zero_sized_frame_is_not_a_division_error():
    assert zones.ground_point([0, 0, 10, 10], 0, 0) is None

# --- the rules ----------------------------------------------------------------


def _zone(name="Driveway", kind=zones.INCLUDE, points=None, labels=("car",)):
    return {
        "id": 1, "camera": "drive", "name": name, "kind": kind,
        "points": points or DRIVEWAY, "labels": labels,
    }


# Wheels at (0.5, 0.95) — inside the driveway wedge.
IN_DRIVE = [400, 500, 200, 450]
# Wheels at (0.5, 0.3) — up in the street.
IN_STREET = [400, 100, 200, 200]


def test_an_include_area_keeps_what_is_inside_and_drops_what_is_not():
    zone_list = [_zone()]
    assert zones.evaluate(zone_list, "car", IN_DRIVE, 1000, 1000) == (True, "Driveway")
    assert zones.evaluate(zone_list, "car", IN_STREET, 1000, 1000) == (False, None)


def test_a_label_no_area_mentions_is_unrestricted():
    """Areas are a statement about the labels they name. A camera with a car zone
    still reports people from anywhere — including the street, which is exactly
    what a camera is for."""
    zone_list = [_zone(labels=("car", "truck"))]
    assert zones.evaluate(zone_list, "person", IN_STREET, 1000, 1000) == (True, None)


def test_an_area_with_no_labels_covers_every_object():
    zone_list = [_zone(labels=())]
    assert zones.evaluate(zone_list, "person", IN_STREET, 1000, 1000) == (False, None)
    assert zones.evaluate(zone_list, "person", IN_DRIVE, 1000, 1000) == (True, "Driveway")


def test_an_ignore_area_beats_an_include_one():
    """The reason ignore exists: a hole in a larger shape, without having to draw
    a concave outline around it by hand."""
    whole_frame = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    corner = [(0.3, 0.8), (0.7, 0.8), (0.7, 1.0), (0.3, 1.0)]
    zone_list = [
        _zone("Everywhere", zones.INCLUDE, whole_frame),
        _zone("Pavement", zones.IGNORE, corner),
    ]
    # (0.5, 0.95) is inside both — ignore wins.
    assert zones.evaluate(zone_list, "car", IN_DRIVE, 1000, 1000) == (False, None)
    # (0.5, 0.3) is inside the include shape only.
    assert zones.evaluate(zone_list, "car", IN_STREET, 1000, 1000) == (True, "Everywhere")


def test_an_ignore_area_works_without_any_include_area():
    """"Never over the neighbour's window" is a complete instruction on its own."""
    zone_list = [_zone("Neighbour", zones.IGNORE, DRIVEWAY)]
    assert zones.evaluate(zone_list, "car", IN_DRIVE, 1000, 1000) == (False, None)
    assert zones.evaluate(zone_list, "car", IN_STREET, 1000, 1000) == (True, None)


def test_the_first_matching_include_area_names_the_detection():
    porch = [(0.0, 0.0), (0.4, 0.0), (0.4, 0.4), (0.0, 0.4)]
    zone_list = [_zone("Porch", points=porch), _zone("Driveway")]
    assert zones.evaluate(zone_list, "car", IN_DRIVE, 1000, 1000) == (True, "Driveway")


def test_an_ignore_area_for_another_label_does_not_touch_this_one():
    zone_list = [_zone("Bins", zones.IGNORE, DRIVEWAY, labels=("person",))]
    assert zones.evaluate(zone_list, "car", IN_DRIVE, 1000, 1000) == (True, None)
    assert zones.evaluate(zone_list, "person", IN_DRIVE, 1000, 1000) == (False, None)


def test_no_areas_at_all_keeps_everything_unnamed():
    assert zones.evaluate([], "car", IN_STREET, 1000, 1000) == (True, None)


def test_filtering_stamps_the_area_onto_what_it_keeps():
    """The stamp is what reaches the database and the event, so an automation can
    fire on "a person in the Porch" rather than on a whole camera."""
    zone_list = [_zone()]
    detections = [
        {"label": "car", "confidence": 0.9, "box": list(IN_STREET)},
        {"label": "person", "confidence": 0.9, "box": list(IN_STREET)},
        {"label": "car", "confidence": 0.9, "box": list(IN_DRIVE)},
    ]
    kept = zones.filter_detections(zone_list, detections, 1000, 1000)
    assert [(d["label"], d["zone"]) for d in kept] == [
        ("person", None), ("car", "Driveway"),
    ]


# --- parsing stored rows ------------------------------------------------------


def _row(**over):
    row = {"id": 3, "camera": "drive", "name": "Driveway", "kind": "include",
           "points": zones.dump_points(DRIVEWAY), "labels": '["car", "TRUCK"]'}
    row.update(over)
    return row


def test_a_stored_row_parses_into_something_the_matcher_can_use():
    zone = zones.parse(_row())
    assert zone["name"] == "Driveway"
    assert zone["kind"] == zones.INCLUDE
    assert zone["labels"] == ("car", "truck"), "labels are normalised on the way in"
    assert len(zone["points"]) == 4


def test_a_mangled_row_is_no_zone_rather_than_a_crash():
    """Read in a camera thread on every detection. It fails open — everything is
    recorded — because a camera that silently records nothing is worse."""
    for bad in ({}, _row(points="not json"), _row(points="[]"),
                _row(points='[[0, 0], [1, 1]]'), _row(points='[["a", 0], [1, 1], [0, 1]]')):
        assert zones.parse(bad) is None


def test_an_unknown_kind_is_treated_as_include():
    """A row that says something this version does not understand must not
    quietly become an ignore area and hide everything."""
    assert zones.parse(_row(kind="banish"))["kind"] == zones.INCLUDE


def test_rows_are_grouped_by_camera():
    grouped = zones.parse_all([_row(), _row(id=4, camera="porch", name="Step")])
    assert set(grouped) == {"drive", "porch"}
    assert grouped["porch"][0]["name"] == "Step"


def test_coordinates_outside_the_frame_are_clamped_not_rejected():
    points = zones.parse_points('[[-0.2, 0.5], [1.4, 0.5], [0.5, 1.2]]')
    assert points == [(0.0, 0.5), (1.0, 0.5), (0.5, 1.0)]


# --- storage and migration ----------------------------------------------------


def test_a_camera_can_have_several_named_areas(db_path):
    import store

    conn = store.connect(db_path, actor="user")
    try:
        store.add_zone(conn, "drive", "Driveway", "include",
                       zones.dump_points(DRIVEWAY), zones.dump_labels(["car"]))
        store.add_zone(conn, "drive", "Pavement", "ignore",
                       zones.dump_points(DRIVEWAY), zones.dump_labels([]))
        conn.commit()
        rows = store.zones(conn, "drive")
        assert [r["name"] for r in rows] == ["Driveway", "Pavement"]
        assert [r["kind"] for r in rows] == ["include", "ignore"]
    finally:
        conn.close()


def test_two_areas_on_one_camera_cannot_share_a_name(db_path):
    """The name is how a detection refers to an area; two of them would make a
    row ambiguous about where it happened."""
    import store

    conn = store.connect(db_path, actor="user")
    try:
        assert store.add_zone(conn, "drive", "Gate", "include",
                              zones.dump_points(DRIVEWAY), "[]") is not None
        assert store.add_zone(conn, "drive", "Gate", "include",
                              zones.dump_points(DRIVEWAY), "[]") is None
        # But two cameras may each have a Gate — they are different gates.
        assert store.add_zone(conn, "porch", "Gate", "include",
                              zones.dump_points(DRIVEWAY), "[]") is not None
    finally:
        conn.close()


def test_a_1_13_camera_zone_becomes_a_named_area(tmp_path):
    """The shape somebody had already drawn has to survive the upgrade, named
    after the camera — which is almost always the area it watches."""
    import json

    import store

    path = str(tmp_path / "old.db")
    store.init_db(path)
    conn = store.connect(path, actor="user")
    try:
        conn.execute("INSERT INTO cameras (id, kind) VALUES ('Driveway', 'rtsp')")
        conn.execute(
            "UPDATE cameras SET zone = ? WHERE id = 'Driveway'",
            (json.dumps({"points": [list(p) for p in DRIVEWAY], "labels": ["car"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    store.init_db(path)          # the upgrade
    conn = store.connect(path, actor="user")
    try:
        rows = store.zones(conn, "Driveway")
        assert len(rows) == 1
        assert rows[0]["name"] == "Driveway"
        assert rows[0]["kind"] == "include"
        assert zones.parse(rows[0])["labels"] == ("car",)
        # Cleared as it goes, so an area somebody later deletes stays deleted
        # rather than coming back on the next boot.
        assert conn.execute("SELECT zone FROM cameras").fetchone()[0] is None
    finally:
        conn.close()

    store.init_db(path)          # and again, to prove it does not duplicate
    conn = store.connect(path, actor="user")
    try:
        assert len(store.zones(conn, "Driveway")) == 1
    finally:
        conn.close()


def test_a_detection_records_the_area_it_was_in(db_path):
    import store

    conn = store.connect(db_path, actor="user")
    try:
        store.record_detections(conn, "drive", [
            {"label": "car", "confidence": 0.9, "box": [1, 2, 3, 4], "zone": "Driveway"},
            {"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]},
        ])
        conn.commit()
        rows = {r["label"]: r["zone"] for r in store.recent_detections(conn)}
        assert rows["car"] == "Driveway"
        assert rows["person"] is None, "no area claimed it, and that is not a blank name"
    finally:
        conn.close()


# --- the API ------------------------------------------------------------------


def _post_zone(client, **over):
    body = {"camera": "drive", "name": "Driveway", "kind": "include",
            "points": [list(p) for p in DRIVEWAY], "labels": ["car"]}
    body.update(over)
    return client.post("/api/zones", json=body)


def test_an_area_can_be_added_listed_edited_and_removed(client, db_path):
    created = _post_zone(client)
    assert created.status_code == 201, created.get_json()
    zone_id = created.get_json()["id"]

    listed = client.get("/api/zones").get_json()["zones"]
    assert [z["name"] for z in listed] == ["Driveway"]
    assert listed[0]["labels"] == ["car"]
    assert listed[0]["usable"] is True

    renamed = client.put(f"/api/zones/{zone_id}", json={"name": "Front drive"})
    assert renamed.status_code == 200
    assert client.get("/api/zones").get_json()["zones"][0]["name"] == "Front drive"

    assert client.delete(f"/api/zones/{zone_id}").status_code == 200
    assert client.get("/api/zones").get_json()["zones"] == []


def test_areas_can_be_listed_for_one_camera(client, db_path):
    _post_zone(client)
    _post_zone(client, camera="porch", name="Step")
    assert len(client.get("/api/zones?camera=porch").get_json()["zones"]) == 1


def test_the_refusals_say_what_is_wrong(client, db_path):
    assert "name" in _post_zone(client, name="  ").get_json()["error"]
    assert "three points" in _post_zone(client, points=[[0, 0], [1, 1]]).get_json()["error"]
    assert "kind" in _post_zone(client, kind="banish").get_json()["error"]
    assert "lorry" in _post_zone(client, labels=["lorry"]).get_json()["error"]
    assert "camera" in _post_zone(client, camera="").get_json()["error"]


def test_a_duplicate_name_on_one_camera_is_a_conflict(client, db_path):
    _post_zone(client)
    assert _post_zone(client).status_code == 409


def test_editing_can_change_one_thing_without_resending_the_shape(client, db_path):
    """A rename should not require the caller to send back a polygon it never
    touched."""
    zone_id = _post_zone(client).get_json()["id"]
    assert client.put(f"/api/zones/{zone_id}", json={"kind": "ignore"}).status_code == 200
    zone = client.get("/api/zones").get_json()["zones"][0]
    assert zone["kind"] == "ignore"
    assert len(zone["points"]) == 4, "the shape survived an edit that did not mention it"


def test_areas_are_reported_per_camera(client, db_path):
    _post_zone(client)
    _post_zone(client, name="Pavement", kind="ignore", labels=[])
    camera = next(c for c in client.get("/api/cameras").get_json()["cameras"]
                  if c["id"] == "drive")
    assert [z["name"] for z in camera["zones"]] == ["Driveway", "Pavement"]


def test_managing_areas_is_behind_the_access_gate(direct_client, db_path):
    assert direct_client.get("/api/zones").status_code == 401
    assert direct_client.post("/api/zones", json={}).status_code == 401
    assert direct_client.delete("/api/zones/1").status_code == 401


def test_the_page_offers_the_area_editor(client, db_path):
    html = client.get("/").get_data(as_text=True)
    assert "Where to look" in html
    assert "only count here" in html and "never count here" in html
    assert "api/zones" in html
    assert 'fetch("/api/zones' not in html, "absolute URL would break ingress"


def test_the_page_draws_areas_on_a_detection(client, db_path):
    """"Why was that car recorded?" is only answerable next to the shape that
    let it through, on the same frame."""
    html = client.get("/").get_data(as_text=True)
    assert "z.camera === det.camera" in html, "areas are not drawn on the snapshot"
    assert "in ${esc(det.zone)}" in html, "the area is not named on a detection"
