# Detection Hub

Object detection for Home Assistant: send it an image, get back what is in it
and where. Runs entirely on the CPU, on this machine — no cloud service, no API
key, nothing leaves the host.

It watches RTSP cameras continuously, answers on demand over HTTP, records what
it finds with a snapshot, tells Home Assistant the moment something appears, and
hands the history to the data pipeline.

## What it does

- Detects the **80 COCO classes** — person, car, truck, bicycle, dog, cat, bird
  and the rest — using **YOLOX-Nano** at 416×416, bundled in the image.
- Answers in **roughly 15–20 ms per image** on a modern CPU. No GPU, no Coral.
- Reports a label, a confidence and a pixel box for each thing it finds.
- Filters to the classes you care about, so a driveway camera is not told about
  the potted plant on every frame.

## Watching cameras

Add one per line under **cameras**, as `name = url`:

```
drive  = rtsp://user:pass@192.168.1.60:554/Streaming/Channels/102
garden = rtsp://user:pass@192.168.1.61:554/h264
```

**Point at the substream if your camera has one.** Motion detection does not need
4K, and decoding it is the single largest cost in this add-on. The `/102` above
is a Hikvision-style substream; most cameras expose something equivalent.

### How it stays affordable on a CPU

A forward pass costs ~15 ms. A 15 fps stream fed straight to the detector is a
quarter of a core spent almost entirely re-deciding that nothing has changed. So
three throttles sit between a camera and the model:

1. **Sampling** — every frame is *read* (a decoder that is not drained backs up
   and starts handing over stale frames) but only **max_fps** of them are
   examined. Default 2.
2. **The motion gate** — a considered frame is compared against the previous one
   at 320 px greyscale. Unchanged frames never reach the detector. On a still
   scene this is the whole cost: a cheap comparison instead of a forward pass.
3. **Collapse repeats** — one event per *object*, not per frame it is seen in.
   A parked car does not move, but wind, shifting light and the camera's own
   exposure keep tripping the gate, so the detector keeps finding the car sitting
   there. Rather than log each of those, an object is matched to the last one of
   its kind by position: same place, same object, logged once and then quiet
   until it leaves. Turn it off with `collapse_repeats` to log every detection.

The first frame after a camera connects always goes to the detector — it has no
baseline to compare against, and a camera pointed at a parked car should report
it rather than wait for it to move.

When a camera connects, the log says so — `camera driveway: connected
(640x360)` — with the stream's resolution, which is the one place it shows up
since the RTSP handshake on many cameras does not advertise it. A working camera
that produced only silence used to be indistinguishable from one that never
started.

If a camera drops, it reconnects with a widening backoff up to a minute, so a
rebooting camera is not retried in a tight loop. A failure to open now names the
likely causes — a rejected password, a wrong path, an unreachable camera —
rather than only echoing ffmpeg's raw error (on some cameras a bad password
even shows as a `406`, which reads like a protocol fault and is not).

## Home Assistant

### Events — for automations

Each detection fires **`detection_hub_detection`** as it happens:

```yaml
automation:
  - alias: "Someone at the door"
    trigger:
      - platform: event
        event_type: detection_hub_detection
        event_data:
          camera: drive
          label: person
    action:
      - service: notify.mobile_app
        data:
          message: >
            {{ trigger.event.data.label }} at {{ trigger.event.data.camera }}
            ({{ trigger.event.data.confidence }})
```

Events rather than states, because a detection happens at an instant. As a state
two in a row look like one, and an automation watching for a change misses the
second entirely.

### A camera going offline

Set **notify_service** to a `notify.*` service (without the `notify.` prefix,
e.g. `mobile_app_pixel`) and you get a push when a camera drops — and another
when it comes back. It fires on the *change*, so a camera down for an hour is one
message, not sixty.

Offline includes a **frozen feed**, not just a dropped connection: a camera that
delivers no frame for **camera_offline_seconds** (default 120) is offline even if
its thread never errored. That is the failure a plain connection check misses.

The first time the add-on sees a camera it records its state without notifying —
a camera still connecting at startup, or misconfigured from the start, should not
page you. The alert is for a camera that was working and stopped. Either way the
state shows on the sensors below and in the log, with or without a notify service.

### Sensors — for dashboards

- `sensor.detection_hub_detections_today` — the count, with a `by_label`
  breakdown. Carries `state_class: measurement`, so the recorder keeps long-term
  statistics rather than only recent states.
- `sensor.detection_hub_cameras_online` — how many are streaming, of how many
  configured.
- `sensor.detection_hub_<camera>_last_seen` — when that camera last detected
  something, with its frame counters.
- `binary_sensor.detection_hub_<camera>_online` — `connectivity`. A camera that
  is up but seeing nothing and one that is down are different situations, and an
  automation should not have to parse a detail string to tell them apart.

Camera names become entity ids with anything non-alphanumeric replaced, so
`back garden` is `..._back_garden_online`.

## The page

The add-on's sidebar page shows **Last detected** — the most recent detection
with its snapshot, camera, confidence and how long ago, and a strip of the ones
before it. It refreshes every ten seconds, so it is a live view of what the
cameras are seeing without leaving Home Assistant. A detection whose snapshot has
already been pruned shows without a thumbnail rather than a broken image.

**Click any snapshot to expand it** — it opens full size with the detection's
bounding box drawn on, plus the label, camera, confidence and timestamp. Click
outside it or press Escape to close.

**Filter what you see.** An **Object** dropdown narrows to one type — person,
car, dog — offering only the types actually detected. A row of time buttons —
**Live · 1h · Today · 7 days · Custom** — picks the window; Live is the
auto-refreshing view, the presets need no calendar, and **Custom** reveals a
From / To date-and-time picker for an exact range down to the minute. The filters
combine, so "cars, today" is two clicks.

Under the hood these are `label`, `from` and `to` on `/api/detections`. `from`
and `to` accept a plain date (`2026-08-09`, the whole day) as well as a
date-and-time (`2026-08-09T18:00`), and `/api/labels` lists the object types seen.

Bear in mind snapshots are pruned on their own schedule
(`snapshot_retention_days`), so an old day may list detections whose images are
already gone — they show without a thumbnail. Detections themselves are kept for
`detection_retention_days`, so a range older than that will be empty.

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
- **`GET /api/snapshots/<id>`** — the stored JPEG for a detection. `404` once
  the image has been pruned, or after a restored backup — see **Storage**.
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
  expectation well inside the age window. In practice the count binds first: with
  a 30-second cooldown, one busy camera reaches 2000 in under a day. At ~68 KB a
  frame that cap is about 136 MB on disk, which `/api/stats` reports as
  `snapshot_bytes`.
- **cameras**: one `name = url` per line. Empty means no continuous watching —
  the API and the try-it page still work. A line without an `=` is skipped
  rather than failing the whole configuration, so one typo does not stop the
  other cameras.
- **max_fps**: `2` (default). Frames per second actually examined per camera.
  `0` means examine every frame.
- **motion_threshold**: `0.005` (default). The fraction of the frame that must
  change before the detector is woken. Raise it if a swaying tree keeps
  triggering; lower it if slow movement is missed.
- **collapse_repeats**: `true` (default). Log an object once while it stays in
  view instead of repeatedly. A parked car logs on arrival and stays quiet until
  it leaves; turn this off to record every detection the gate lets through.
- **cooldown_seconds**: `30` (default). How long an object must be **absent**
  from the frames the detector examines before it is treated as gone, so a later
  reappearance is a new event. Only relevant while `collapse_repeats` is on.
- **ha_sensors_enabled** / **ha_events_enabled**: both on. Turn events off if
  you only want the history and the lakehouse — that saves an HTTP call per
  detection.
- **sensor_prefix**: `detection_hub` (default). The entity-id prefix.
- **notify_service**: a `notify.*` service (no `notify.` prefix) for
  camera-offline push alerts. Empty means no push — the state still shows on the
  sensors and in the log.
- **camera_offline_seconds**: `120` (default). How long without a frame before a
  camera counts as offline. Covers a hung feed, not just a dropped connection.
- **model**: `nano` (default) or `tiny`. Both are YOLOX at 416×416 and bundled
  in the image, so switching changes nothing else and needs no internet — only a
  restart, since the model is loaded once at start. Which model is running shows
  on the page's status card and in `/api/debug`.

  **Pick `tiny` only if objects are being missed at distance.** Measured on a
  real driveway, 9 frames: both models found the near car every time (nano 0.88,
  tiny 0.90), but a **second, further car** sat right on the 0.6 threshold for
  nano — found in 2 frames at 0.60–0.64 — while tiny found it in 3 and scored it
  as high as 0.86. That marginal case is the entire difference. The cost is
  roughly **two to four times the CPU per analysed frame** (12 ms against 27 ms
  on one machine, 14 against 51 on another), which motion gating keeps
  affordable: nothing is spent while the scene is still.
- **model_path**: overrides `model` entirely. Empty uses the bundled choice. Point it at another ONNX
  file to swap models; it must be a YOLOX export at the same input size, and the
  add-on refuses one whose output shape disagrees rather than producing boxes in
  the wrong places.

Set these on the add-on's **Configuration** tab, then restart the add-on.

## Storage

Detections, cameras and the change log live in SQLite at
`/data/detections.db`. **Snapshots do not** — they are files under
`/data/snapshots/`, indexed by a row that holds only their size and dimensions.

That split exists for one reason: Home Assistant copies `/data` into every
backup. At ~68 KB per 768×576 frame, the 2000-image default would put ~136 MB of
pictures into every backup — and a 1080p stream roughly four times that — for
images that were glanced at once. `config.yaml` therefore carries:

```yaml
backup_exclude:
  - "snapshots/**"
```

So **a restored backup brings back every detection and none of the pictures**.
`/api/snapshots/<id>` returns 404 for those, which is a normal state rather than
a fault. `/api/stats` reports `db_bytes` and `snapshot_bytes` separately,
because the two are backed up differently and one combined figure would hide
exactly the distinction that matters.

The database runs in **WAL mode** with `synchronous=NORMAL`. Measured on four
concurrent writers, that is ~2,160 commits/s against ~1,500 on the default
rollback journal — a 44% gain. Worth having, but it is tuning rather than a
repair: the default configuration lost nothing under contention.

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

**YOLOX**, Apache-2.0. Nano (3.6 MB) is the default; Tiny (20 MB) is bundled
beside it and chosen with the `model` option above.

The obvious choices are YOLOv5 and YOLOv8, and both are Ultralytics **AGPL-3.0**
— the wrong licence to vendor into a public repository. YOLOX is Apache-2.0 and
performs comparably at this size.

Nano is the default on speed. Measured on one frame at the outset, it returned
the same people and vehicles in **14 ms** against Tiny's **51 ms**, at a fifth
of the file size — which read at the time as faster for no loss that mattered.
Nine live driveway frames later showed what a single frame could not: a second,
more distant car that sat right on the confidence threshold for Nano and well
above it for Tiny. That is a real difference, and it is why Tiny is now bundled
and selectable by name rather than something you have to source and mount
yourself. It is still not the default, because the cost is two to four times the
CPU per analysed frame and most scenes never present that marginal object — see
the `model` option for the numbers.

Both models are baked into the image, so the add-on needs no internet at any
point.

## Notes

- **Nothing is stored unless you ask.** A detection is computed, returned and
  forgotten unless the call names a `camera`.
- **Writes are batched, not per detection.** This host measures 286 durable
  commits a second, a budget the pipeline's Postgres shares. A commit per
  detection would compete with it directly and show up as rising disk write
  latency while utilisation stayed flat.
- **CPU only, by design.** Home Assistant OS makes GPU passthrough to add-ons
  awkward, and a 15 ms model does not need one. One or two streams is the
  intended scale; six is not.
- **RTSP is forced over TCP.** The default lets ffmpeg negotiate UDP, which over
  wifi drops packets and produces torn frames — the motion gate reads those as
  movement and wakes the detector for artefacts, all night.
- **The Add-on Watchdog is told the truth.** This add-on writes
  `/share/pipeline-status/detection-hub.json` every minute with its camera and
  detector state. A probe of the web UI cannot see a dead capture thread — the
  page answers perfectly well while nothing is being watched — which is exactly
  the failure that convention exists for.
- **amd64 only.** opencv publishes aarch64 wheels, but a Pi-class board cannot
  carry what the later releases will ask of this, and offering it there would be
  offering something that does not work.
- The first inference after a restart is a little slower than the rest — the
  model is read from disk at startup, but OpenCV lays out its buffers on first
  use.
