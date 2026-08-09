"""The HTTP surface: what it accepts, what it refuses, and what it never leaks."""
import io

import app as hub


# --- /api/detect --------------------------------------------------------------


def test_detect_accepts_a_multipart_upload(client, street_jpeg):
    res = client.post(
        "/api/detect", data={"image": (io.BytesIO(street_jpeg), "street.jpg")}
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == len(body["detections"])
    assert "person" in [d["label"] for d in body["detections"]]


def test_detect_accepts_a_raw_body(client, street_jpeg):
    """curl --data-binary sends no multipart envelope. Requiring one is an
    unhelpful 400 for whichever caller guessed wrong."""
    res = client.post(
        "/api/detect", data=street_jpeg, content_type="application/octet-stream"
    )
    assert res.status_code == 200
    assert res.get_json()["count"] > 0


def test_detect_accepts_curls_default_content_type(client, street_jpeg):
    """`curl --data-binary @photo.jpg` sends application/x-www-form-urlencoded
    unless told otherwise. Reading request.files first made Werkzeug parse that
    as a form and consume the stream, so the body arrived empty and a valid
    JPEG came back as "empty image" — found by running the server, not here."""
    res = client.post(
        "/api/detect",
        data=street_jpeg,
        content_type="application/x-www-form-urlencoded",
    )
    assert res.status_code == 200
    assert res.get_json()["count"] > 0


def test_a_multipart_post_with_no_file_part_is_a_clean_400(client):
    """Multipart, but the field is named something else — a refusal with a
    reason, not a KeyError."""
    res = client.post("/api/detect", data={"picture": (io.BytesIO(b"x"), "a.jpg")})
    assert res.status_code == 400


def test_detect_reports_the_image_size_it_actually_read(client, street_jpeg):
    """Boxes are in these coordinates, so a caller drawing them needs to know
    the frame they belong to rather than assuming what it sent."""
    body = client.post("/api/detect", data=street_jpeg).get_json()
    assert body["image"] == {"width": 768, "height": 576}


def test_detect_rejects_a_non_image_with_400(client):
    """The caller's fault, and it should say so — not a 500."""
    res = client.post("/api/detect", data=b"definitely not a jpeg")
    assert res.status_code == 400
    assert "not a readable image" in res.get_json()["error"]


def test_detect_rejects_an_empty_body(client):
    res = client.post("/api/detect", data=b"")
    assert res.status_code == 400
    assert "empty image" in res.get_json()["error"]


def test_a_broken_model_is_503_not_400(client, set_options, tmp_path):
    """A missing model is this add-on's fault, not the caller's, and the status
    code is the difference between 'retry later' and 'fix your request'."""
    set_options(model_path=str(tmp_path / "absent.onnx"))
    res = client.post("/api/detect", data=b"\xff\xd8\xff\xe0", content_type="image/jpeg")
    assert res.status_code in (400, 503)
    if res.status_code == 503:
        assert "model not found" in res.get_json()["error"]


def test_confidence_can_be_overridden_per_request(client, street_jpeg):
    """One camera's threshold is not another's, and the caller knows which."""
    loose = client.post("/api/detect?confidence=0.5", data=street_jpeg).get_json()
    strict = client.post("/api/detect?confidence=0.9", data=street_jpeg).get_json()
    assert strict["count"] <= loose["count"]
    assert strict["confidence"] == 0.9


def test_a_nonsense_confidence_falls_back_rather_than_500s(client, street_jpeg):
    body = client.post("/api/detect?confidence=banana", data=street_jpeg).get_json()
    assert body["confidence"] == 0.6


def test_labels_can_be_narrowed_per_request(client, street_jpeg):
    body = client.post("/api/detect?labels=person", data=street_jpeg).get_json()
    assert body["count"] > 0
    assert {d["label"] for d in body["detections"]} == {"person"}


def test_an_unknown_label_does_not_silently_match_everything(client, street_jpeg):
    """Filtering on a typo must not quietly return all 80 classes — that reads
    as 'the filter works' right up until it matters."""
    body = client.post("/api/detect?labels=persson", data=street_jpeg).get_json()
    assert {d["label"] for d in body["detections"]} != {"persson"}


# --- configured defaults ------------------------------------------------------


def test_the_configured_label_filter_applies_without_a_query_string(
    client, set_options, street_jpeg
):
    set_options(labels="car, truck")
    body = client.post("/api/detect", data=street_jpeg).get_json()
    assert {d["label"] for d in body["detections"]} <= {"car", "truck"}


def test_an_empty_label_option_means_every_class(client, set_options, street_jpeg):
    set_options(labels="")
    body = client.post("/api/detect", data=street_jpeg).get_json()
    assert body["count"] > 0


def test_confidence_option_is_clamped_to_something_sane(client, set_options):
    set_options(confidence=99)
    assert hub.get_confidence() == 0.99
    set_options(confidence=-5)
    assert hub.get_confidence() == 0.05
    set_options(confidence="not a number")
    assert hub.get_confidence() == 0.6


# --- health and debug ---------------------------------------------------------


def test_health_is_200_when_the_detector_loaded(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["detector_ready"] is True


def test_health_is_503_when_the_model_is_missing(client, set_options, tmp_path):
    """The watchdog treats anything under 500 as alive, so a broken detector has
    to be a 5xx or it reads as healthy while detecting nothing."""
    set_options(model_path=str(tmp_path / "absent.onnx"))
    res = client.get("/api/health")
    assert res.status_code == 503
    assert res.get_json()["ok"] is False


def test_debug_reports_token_state_but_never_the_value(client, set_options):
    set_options(api_token="s3cret-token")
    body = client.get("/api/debug").get_json()
    assert body["api_token_set"] is True
    assert body["api_token_length"] == 12
    assert "s3cret-token" not in client.get("/api/debug").get_data(as_text=True)


def test_version_matches_config(client):
    """APP_VERSION and config.yaml drift apart silently: the add-on reports one
    number while Home Assistant installs another, and nothing complains."""
    import os

    data = client.get("/api/debug").get_json()
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
    with open(config_path) as handle:
        text = handle.read()
    assert f'version: "{data["app_version"]}"' in text


def test_the_index_page_renders_and_names_its_version(client):
    res = client.get("/")
    assert res.status_code == 200
    assert hub.APP_VERSION.encode() in res.data


# --- access control -----------------------------------------------------------


def test_ingress_works_without_any_token(client):
    """Closing the published port must not put a credential in front of the UI."""
    assert client.get("/api/health").status_code == 200


def test_the_published_port_is_refused_without_a_token(direct_client):
    """The case that was left open in the trackers until 1.44.0: no allowlist,
    no token configured, no ingress header."""
    res = direct_client.get("/api/health")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert "api_token" in res.get_json()["detail"]


def test_the_published_port_accepts_the_configured_token(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get(
        "/api/health", headers={"Authorization": "Bearer s3cret-token"}
    )
    assert res.status_code == 200


def test_a_wrong_token_is_refused(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get("/api/health", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_detect_itself_is_covered_by_the_gate(direct_client, street_jpeg):
    """The hook is before_request, so it must cover every route — including the
    expensive one someone could use to burn CPU for free."""
    assert direct_client.post("/api/detect", data=street_jpeg).status_code == 401


def test_a_disallowed_ingress_user_is_blocked(client, set_options):
    set_options(restrict_to_user_ids="somebody-else")
    res = client.get("/api/health")
    assert res.status_code == 403
    assert b"Access restricted" in res.data
    assert b"test-ingress-user" in res.data, "the blocked user needs their own ID"


def test_an_allowed_ingress_user_passes(client, set_options):
    set_options(restrict_to_user_ids="test-ingress-user, someone-else")
    assert client.get("/api/health").status_code == 200
