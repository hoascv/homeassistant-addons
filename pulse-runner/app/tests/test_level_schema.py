"""Pure validation of the level object shape — no Flask, no DB."""
import pytest

from app import validate_level_objects, validate_level_payload


def _payload(**overrides):
    payload = {
        "name": "Level",
        "start_mode": "cube",
        "scroll_speed": 8,
        "background": "grid-blue",
        "length_units": 20,
        "objects": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "objects",
    [
        [],
        [{"type": "block", "x": 0, "y": 0, "w": 5, "h": 1}],
        [{"type": "spike", "x": 1.5, "y": 1, "w": 1, "h": 1}],
        [{"type": "pad", "x": 0, "y": 0, "variant": "yellow"}],
        [{"type": "orb", "x": 0, "y": 0, "variant": "blue"}],
        [{"type": "portal", "x": 0, "y": 0, "kind": "gravity", "h": 2}],
        [{"type": "portal", "x": 0, "y": 0, "kind": "mode", "h": 2, "value": "ship"}],
        [{"type": "checkpoint", "x": 0, "y": 0, "order": 1}],
        [{"type": "decoration", "x": 0, "y": 0, "kind": "star"}],
    ],
)
def test_valid_objects_pass(objects):
    validate_level_objects(objects)  # must not raise


@pytest.mark.parametrize(
    "objects,expected_fragment",
    [
        ("not a list", "must be a list"),
        ([{"x": 0, "y": 0}], "unknown type"),
        ([{"type": "laser", "x": 0, "y": 0}], "unknown type"),
        ([{"type": "block", "y": 0, "w": 1, "h": 1}], "'x'"),
        ([{"type": "block", "x": 0, "y": 0}], "'w'"),
        ([{"type": "block", "x": 0, "y": 0, "w": 1}], "'h'"),
        ([{"type": "spike", "x": 0, "y": 0, "w": "wide", "h": 1}], "'w'"),
        ([{"type": "pad", "x": 0, "y": 0}], "'variant'"),
        ([{"type": "checkpoint", "x": 0, "y": 0, "order": "first"}], "'order'"),
        ([{"type": "portal", "x": 0, "y": 0, "kind": "teleport", "h": 1}], "kind must be one of"),
        ([{"type": "portal", "x": 0, "y": 0, "h": 1}], "'kind'"),
        (["not-a-dict"], "must be an object"),
    ],
)
def test_invalid_objects_raise(objects, expected_fragment):
    with pytest.raises(ValueError, match=expected_fragment.replace("(", r"\(").replace(")", r"\)")):
        validate_level_objects(objects)


def test_valid_payload_passes():
    validate_level_payload(_payload(objects=[{"type": "block", "x": 0, "y": 0, "w": 5, "h": 1}]))


def test_payload_requires_name():
    with pytest.raises(ValueError, match="name"):
        validate_level_payload(_payload(name=""))


def test_payload_rejects_unknown_start_mode():
    with pytest.raises(ValueError, match="start_mode"):
        validate_level_payload(_payload(start_mode="unicycle"))


def test_payload_rejects_non_positive_scroll_speed():
    with pytest.raises(ValueError, match="scroll_speed"):
        validate_level_payload(_payload(scroll_speed=0))


def test_payload_rejects_non_positive_length_units():
    with pytest.raises(ValueError, match="length_units"):
        validate_level_payload(_payload(length_units=-5))


def test_payload_propagates_object_errors():
    with pytest.raises(ValueError, match="unknown type"):
        validate_level_payload(_payload(objects=[{"type": "laser", "x": 0, "y": 0}]))
