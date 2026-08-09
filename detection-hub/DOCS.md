# Detection Hub

Object detection for Home Assistant: send it an image, get back what is in it
and where. Runs entirely on the CPU, on this machine — no cloud service, no API
key, nothing leaves the host.

> **Being built in stages.** Today it detects on demand, remembers what it saw,
> and hands that to the data pipeline. Live cameras and the Home Assistant
> sensors and events are the releases that follow.

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

An image in, detections out. Accepts either a multipart upload under `image` or
the raw bytes as the request body:

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

Query parameters:

- `?confidence=0.8` — raise or lower the threshold for this image.
- `?labels=person,dog` — report only these classes.
- `?camera=front_door` — **record** this detection under that name, with a
  snapshot. Without it nothing is stored, which is what keeps the try-it page
  from filling the database. With it, the HTTP API is an input source on the
  same footing as a camera, and what it records reaches the lakehouse.

Images are capped at 20 MB.

### Reading what it saw

- **`GET /api/detections?camera=&limit=`** — recent rows, newest first.
- **`GET /api/snapshots/<id>`** — the stored JPEG for a detection.
- **`GET /api/cameras`** — every source that has reported, and its last state.

### Feeding a pipeline

The same three endpoints the trackers expose, so `pipeline-airflow` ingests this
add-on with no new machinery:

- **`GET /api/export`** — every tracked table plus the `max_seq` it corresponds
  to. The bootstrap, and what to fall back to after a gap.
- **`GET /api/changes?since=<seq>&limit=<n>`** — everything after a watermark.
  The steady state. `full_reload_required` tells a consumer that has fallen
  further behind than the retained history to bootstrap instead of silently
  skipping rows.
- **`GET /api/stats`** — row counts and database size **without serialising a
  single row**, so the Add-on Watchdog can ask how much data there is once a
  minute without being handed the data.

**Snapshots are deliberately not in the feed.** Images live in their own table,
which keeps a change event small by construction rather than by remembering to
filter one out. A consumer that wants the picture asks for it by id.

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
- **detection_retention_days**: `30` (default). How long a detection row is
  kept.
- **snapshot_retention_days**: `7` (default) / **snapshot_max_count**: `2000`.
  Images are bounded by age *and* count, because either alone fails — a quiet
  week keeps images longer than you meant, and a busy hour blows past any size
  expectation well inside the age window. `/data` is inside Home Assistant's
  backups, which is what makes the ceiling matter rather than just being tidy.
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

- **Nothing is stored unless you ask.** A detection is computed, returned and
  forgotten unless the call names a `camera`.
- **Writes are batched, not per detection.** This host measures 286 durable
  commits a second, a budget the pipeline's Postgres shares. A commit per
  detection would compete with it directly and show up as rising disk write
  latency while utilisation stayed flat.
- **CPU only, by design.** Home Assistant OS makes GPU passthrough to add-ons
  awkward, and a 15 ms model does not need one.
- **amd64 only.** opencv publishes aarch64 wheels, but a Pi-class board cannot
  carry what the later releases will ask of this, and offering it there would be
  offering something that does not work.
- The first inference after a restart is a little slower than the rest — the
  model is read from disk at startup, but OpenCV lays out its buffers on first
  use.
