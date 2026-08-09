# Detection Hub

Object detection for Home Assistant: send it an image, get back what is in it
and where. Runs entirely on the CPU, on this machine — no cloud service, no API
key, nothing leaves the host.

> **This is the first release, and it is the foundation rather than the whole
> thing.** Today it detects on demand through an HTTP API and a drop-an-image
> page. Cameras, stored detections, Home Assistant sensors and events, and the
> feed into the data pipeline are the releases that follow. What is here is the
> part everything else depends on, shipped once it was proven rather than
> alongside a lot of untested plumbing.

## What it does

- Detects the **80 COCO classes** — person, car, truck, bicycle, dog, cat, bird
  and the rest — using **YOLOX-Nano** at 416×416, bundled in the image.
- Answers in **roughly 15–20 ms per image** on a modern CPU. No GPU, no Coral.
- Reports a label, a confidence and a pixel box for each thing it finds.
- Filters to the classes you care about, so a driveway camera is not told about
  the potted plant on every frame.

## Trying it

Open the add-on from the sidebar and drop a photo onto the page. It draws the
boxes over your image and lists what it found. That is the whole UI, and it
exists mainly so you can sanity-check the detector before wiring anything to it.

## The API

Every endpoint requires authentication — see **Access** below.

### `POST /api/detect`

An image in, detections out. Nothing is stored. Accepts either a multipart
upload under `image` or the raw bytes as the request body:

```bash
curl -H "Authorization: Bearer <api_token>" \
     --data-binary @photo.jpg \
     http://homeassistant.local:8099/api/detect
```

```json
{
  "detections": [
    {"label": "person", "confidence": 0.851, "box": [642, 239, 40, 83]},
    {"label": "car",    "confidence": 0.801, "box": [731, 51, 35, 42]}
  ],
  "count": 2,
  "image": {"width": 768, "height": 576},
  "confidence": 0.6,
  "latency_ms": 17.0
}
```

`box` is `[x, y, width, height]` in the pixel coordinates of the image **you
sent** — which is why `image` reports the size it read rather than leaving you
to assume it.

Two query parameters override the configured defaults for one call:

- `?confidence=0.8` — raise or lower the threshold for this image.
- `?labels=person,dog` — report only these classes.

Images are capped at 20 MB.

### `GET /api/health`

`200` with `{"ok": true}` when the model is loaded, **`503` when it is not**.
That distinction matters: the Add-on Watchdog treats any status below 500 as
alive, so a detector that cannot load its model has to answer 5xx or it would
read as perfectly healthy while detecting nothing.

### `GET /api/debug`

Version, whether a token is configured (never the token itself), the resolved
confidence and label filter, and the detector's own state including the model
path and any load error.

## Configuration

- **api_token**: a bearer token for callers outside Home Assistant. Empty by
  default. See **Access**.
- **restrict_to_user_ids**: comma-separated Home Assistant user IDs allowed to
  open the page. Empty means any user who can reach the sidebar entry. Your own
  ID is shown on `/api/debug`, and a blocked user is shown theirs.
- **confidence**: `0.6` (default). The minimum score to report something.
  Deliberately not lower — at `0.35` a dark post in the test footage scored
  `0.50` as a person, and a false "someone is at the door" at 3am costs more
  than a miss the next frame catches anyway. Raise it if you get false
  positives; lower it if small or distant objects are missed.
- **labels**: which classes to report. Defaults to
  `person, car, truck, bicycle, motorcycle, dog, cat, bird`. Empty means all 80,
  which is noisier than it sounds — `chair`, `potted plant` and `tv` fire
  constantly indoors and bury the ones you would automate on. Unknown names are
  ignored rather than matching nothing.
- **model_path**: empty uses the bundled YOLOX-Nano. Point it at another ONNX
  file to swap models; it must be a YOLOX export at the same input size, and the
  add-on refuses one whose output shape disagrees rather than producing boxes in
  the wrong places.

Set these on the add-on's **Configuration** tab, then restart the add-on.

## Access

Two ways in, authenticated differently.

**Through the sidebar (ingress)** Home Assistant has already authenticated you,
so nothing further is needed. `restrict_to_user_ids` narrows it to named people
if you want that.

**Through the published port** nothing has authenticated anybody, so
`api_token` is required — including when no token is configured, in which case
every request on that port is refused. "No credential is set" cannot mean "no
check is needed"; that reading is what left the trackers' backup endpoints open
until Coop Tracker 1.44.0. Refusals are `401` with a body naming the option to
set.

The port is **not published by default**. You only need it if something outside
Home Assistant is calling the API — map it under the add-on's Network section.

The startup log says which state you are in without ever printing the token:

```
API token auth: ON — api_token is set (32 characters). The published port accepts it and nothing else.
```

## Why this model

**YOLOX-Nano**, 3.6 MB, Apache-2.0.

The obvious choices are YOLOv5 and YOLOv8, and both are Ultralytics **AGPL-3.0**
— the wrong licence to vendor into a public repository. YOLOX is Apache-2.0 and
performs comparably at this size.

Nano over the larger YOLOX-Tiny after measuring both on the same frame: Nano
returned the same people and vehicles in **14 ms** against Tiny's **51 ms**, at a
fifth of the file size. For a detector that will eventually run against a live
camera, 3.6× faster for no loss that mattered was not a close decision. You can
still swap Tiny in via `model_path` if your scene needs the accuracy.

The model is baked into the image, so the add-on needs no internet at any point.

## Notes

- **Nothing is stored.** This release keeps no database and writes no images.
  A detection is computed, returned and forgotten.
- **CPU only, by design.** Home Assistant OS makes GPU passthrough to add-ons
  awkward, and a 15 ms model does not need one.
- **amd64 only.** opencv publishes aarch64 wheels, but a Pi-class board cannot
  carry what the later releases will ask of this, and offering it there would be
  offering something that does not work.
- The first inference after a restart is a little slower than the rest — the
  model is read from disk at startup, but OpenCV lays out its buffers on first
  use.
