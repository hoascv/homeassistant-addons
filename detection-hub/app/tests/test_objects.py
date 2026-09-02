"""Recognising objects the user taught it, in regions motion proposed.

Two measurements drove this design and both are worth keeping in front of
whoever reads it next.

**Why motion proposes the boxes.** Asked to find a cargo bike, YOLOX-S returned
a single box covering 91% of the frame labelled "tv" at 0.44, and YOLOX-Nano
returned an 84px-wide sliver labelled "person" at 0.73. Neither model boxed the
object, at any confidence, with or without the cargo box cropped away. Region
proposal for a class the detector has never seen does not work, so the proposal
has to come from somewhere that does not care what the object is.

**Why the vectors are centred.** SqueezeNet's 1000-d output has a direction
every crop shares. On a real frame, two crops of the same object scored 0.995
against each other and a crop of that object against the cobbles beside it
scored 0.957 — a gap of 0.038, which is no gap at all. Centred, the same pairs
score 0.774 and -0.758.
"""
import numpy as np
import pytest

import detector
import objects

cv2 = pytest.importorskip("cv2")


# --- proposing regions --------------------------------------------------------


def _frame(value=90):
    return np.full((480, 640, 3), value, np.uint8)


def test_a_changed_patch_becomes_a_box():
    before = _frame()
    after = before.copy()
    cv2.rectangle(after, (200, 150), (360, 330), (230, 230, 230), -1)

    [box] = detector.motion_regions(before, after)
    x, y, w, h = box
    # Dilation and blur widen it, which is wanted: a crop needs its context.
    assert 150 < x < 210 and 100 < y < 160
    assert 150 < w < 260 and 170 < h < 280


def test_two_separate_changes_stay_two_regions():
    before = _frame()
    after = before.copy()
    cv2.rectangle(after, (60, 60), (150, 180), (230, 230, 230), -1)
    cv2.rectangle(after, (420, 260), (560, 420), (230, 230, 230), -1)
    assert len(detector.motion_regions(before, after)) == 2


def test_touching_changes_are_merged():
    """A person walking produces a torso and two legs. Three crops of one
    person are three chances to be wrong about it."""
    before = _frame()
    after = before.copy()
    for x in (200, 240, 280):
        cv2.rectangle(after, (x, 150), (x + 25, 330), (230, 230, 230), -1)
    assert len(detector.motion_regions(before, after)) == 1


def test_an_unchanged_frame_proposes_nothing():
    before = _frame()
    assert detector.motion_regions(before, before.copy()) == []


def test_no_baseline_proposes_nothing():
    """motion_score lets the first frame through so detection can start. There
    is nothing to crop from it, though — everything changed, by definition."""
    assert detector.motion_regions(None, _frame()) == []


def test_a_speck_is_not_a_region():
    """Sensor noise and a moth. min_area is what stops every frame at dusk
    filling the review queue."""
    before = _frame()
    after = before.copy()
    cv2.rectangle(after, (300, 200), (306, 206), (230, 230, 230), -1)
    assert detector.motion_regions(before, after) == []


def test_regions_are_in_the_original_frame_coordinates():
    """The difference runs at 320px wide; the crop is taken from the full
    frame. A box left in the small image's coordinates would crop the wrong
    corner of the picture."""
    before = np.full((996, 1552, 3), 90, np.uint8)
    after = before.copy()
    cv2.rectangle(after, (900, 500), (1200, 800), (230, 230, 230), -1)
    [(x, y, w, h)] = detector.motion_regions(before, after)
    assert x > 640, "the box was not scaled back up"
    assert x + w <= 1552 and y + h <= 996


# --- the embedding space ------------------------------------------------------


@pytest.fixture(scope="module")
def embedder():
    emb = objects.ObjectEmbedder()
    if not emb.status()["available"]:
        pytest.skip(f"embedder unavailable: {emb.status()['error']}")
    return emb


def _patch(seed, size=240):
    """A repeatable textured patch. Random noise embeds distinctly enough to
    stand in for different objects."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (12, 12, 3), dtype=np.uint8)
    return cv2.resize(small, (size, size), interpolation=cv2.INTER_NEAREST)


def test_the_same_image_embeds_identically(embedder):
    patch = _patch(1)
    assert np.allclose(embedder.embed(patch), embedder.embed(patch))


def test_an_embedding_is_unit_length(embedder):
    assert float(np.linalg.norm(embedder.embed(_patch(2)))) == pytest.approx(1.0, abs=1e-5)


def test_an_empty_crop_embeds_to_nothing(embedder):
    assert embedder.embed(None) is None
    assert embedder.embed(np.zeros((0, 0, 3), np.uint8)) is None


def test_centring_is_what_makes_the_space_usable(embedder):
    """The measurement that decided the design. Raw, everything scores 0.94+
    against everything; the classifier would match a wall to a bicycle."""
    vectors = [embedder.embed(_patch(seed)) for seed in range(6)]
    raw = np.stack(vectors)
    raw_spread = float(np.dot(raw[0], raw[1]))

    mean = objects.mean_vector([(0, v) for v in vectors])
    centred = [objects.centre(v, mean) for v in vectors]
    centred_spread = float(np.dot(centred[0], centred[1]))

    assert raw_spread > 0.85, "raw similarity is not as crowded as expected"
    assert centred_spread < raw_spread - 0.3, "centring did not spread the space"


def test_centring_without_a_mean_is_a_no_op(embedder):
    """A first enrolment has nothing to centre against yet."""
    vector = embedder.embed(_patch(3))
    assert np.allclose(objects.centre(vector, None), vector)


# --- naming a region ----------------------------------------------------------


def _enrolled(embedder, seeds_by_class):
    prints = [(object_id, embedder.embed(_patch(seed)))
              for object_id, seeds in seeds_by_class.items() for seed in seeds]
    return prints, objects.mean_vector(prints)


def test_a_trained_object_is_named(embedder):
    prints, mean = _enrolled(embedder, {1: [10, 11, 12], 2: [20, 21, 22]})
    result = objects.identify(embedder.embed(_patch(10)), prints, mean)
    assert result["object_id"] == 1
    assert result["score"] > objects.DEFAULT_THRESHOLD


def test_an_untrained_object_is_not_named(embedder):
    """The question this feature was refined around. A two-class classifier has
    to answer one of its two classes; this one does not, because a wrong name
    on a security alert is worse than no name."""
    prints, mean = _enrolled(embedder, {1: [10, 11], 2: [20, 21]})
    result = objects.identify(embedder.embed(_patch(99)), prints, mean)
    assert result["object_id"] is None


def test_the_score_is_reported_even_when_nothing_matched(embedder):
    """It is the number that says whether the threshold is wrong or the thing
    is genuinely new, and without it a user tuning this is guessing."""
    prints, mean = _enrolled(embedder, {1: [10, 11]})
    result = objects.identify(embedder.embed(_patch(99)), prints, mean,
                              threshold=0.99)
    assert result["object_id"] is None
    assert result["score"] is not None


def test_a_close_call_between_two_classes_abstains(embedder):
    """Two classes a hair apart is a coin flip, and a coin flip that prints a
    name is worse than saying nothing."""
    prints, mean = _enrolled(embedder, {1: [10], 2: [10]})  # identical training
    result = objects.identify(embedder.embed(_patch(10)), prints, mean)
    assert result["object_id"] is None


def test_nothing_enrolled_names_nothing(embedder):
    result = objects.identify(embedder.embed(_patch(1)), [], None)
    assert result["object_id"] is None


def test_a_missing_embedding_names_nothing(embedder):
    prints, mean = _enrolled(embedder, {1: [10, 11]})
    assert objects.identify(None, prints, mean)["object_id"] is None


# --- cropping -----------------------------------------------------------------


def test_a_crop_carries_context_around_the_box():
    """The wall and the kerb are part of what the embedding keys on, and on a
    fixed camera they are stable signal rather than noise."""
    image = np.full((400, 600, 3), 90, np.uint8)
    crop = objects.crop_region(image, [200, 150, 100, 100], margin=0.2)
    assert crop.shape[0] > 100 and crop.shape[1] > 100


def test_a_crop_at_the_edge_is_clipped_not_wrapped():
    image = np.full((400, 600, 3), 90, np.uint8)
    crop = objects.crop_region(image, [560, 360, 80, 80], margin=0.5)
    assert crop is not None and crop.size > 0


def test_a_degenerate_box_crops_to_nothing():
    image = np.full((400, 600, 3), 90, np.uint8)
    assert objects.crop_region(image, [0, 0, 0, 0]) is None
    assert objects.crop_region(None, [0, 0, 10, 10]) is None
