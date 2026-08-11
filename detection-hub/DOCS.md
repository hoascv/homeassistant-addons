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
- Optionally **identifies people by face** and records their name — see
  [Identifying people](#identifying-people). Off by default, and it needs a
  camera close enough to show a face tens of pixels wide.

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

## Identifying people

Detection answers *something is there*. Identification answers *who*, by
comparing a face against the ones you have enrolled. It is **off by default** —
turn it on with `identify_people` — because it processes biometric data, costs
CPU on top of detection, and **on many cameras it cannot work at all**.

### Check your camera first

Face recognition needs pixels. The recogniser works from a 112×112 aligned crop,
so a face has to be tens of pixels wide before there is anything to compare;
below that there is no honest answer to give. Whether your camera ever produces
one is a property of the camera, not of this add-on.

The page has a **Can it identify people?** card. Stand in front of the camera,
wait for the detection, and press *Check the last person seen*. It answers in
plain words and gives you the numbers:

```
person 1: box 218x289 px  →  face 67 px (score 0.94)   ✓ identifiable
person 1: box 96x214 px   →  face 34 px (score 0.81)   ✗ the largest face is
          34 px wide; at least 60 is needed to identify reliably
```

Measured here, so you know what to expect: a person filling a **218×289 px** box
showed a **67 px** face — over the floor, but only just. The frame this
repository ships as a test fixture, where people are 74–87 px tall, produces **no
face at all**. That is the same geometry as a camera watching a driveway or a
street: it will find people all day and identify none of them. A doorbell or a
porch camera is where this works.

### Enrolling somebody

Open a detection of them on the page and choose **Who is this?** — pick an
existing person or type a new name, and the face in that snapshot becomes a
print for them.

Enrol from the add-on's own snapshots rather than a photo from your phone. The
print has to match what the camera will actually deliver: the same lens, angle,
compression and lighting. A studio-quality selfie enrolled against a 60 px night
frame is the classic way this feature appears to work and never fires.

**Enrol several times.** Prints exist to cover variation — hat, glasses, dark,
different angle — and a person is scored by their *best* print, so extra ones can
only help. The People card shows how many each person has and the smallest face
among them, which is the number that predicts whether they will be recognised at
a distance.

Uploads work too, for bootstrapping before any snapshot exists:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
     --data-binary @face.jpg http://homeassistant.local:8099/api/people/1/prints
```

### What it will and will not claim

A match needs two things: a score at or above `face_match_threshold`, **and** a
lead of at least `face_margin` over the next best person. Two people scoring 0.47
and 0.46 is a coin flip, and a coin flip that prints somebody's name is worse
than silence.

Every detection of a person therefore ends in one of four states, and they are
deliberately different things to read:

| state | means | what to do about it |
|---|---|---|
| *(blank)* | no face pass ran — identification off, or not a person | — |
| **no face seen** | it looked until its attempts ran out and never saw a face big enough | camera too far, too dark, or they never faced it |
| **unrecognised** | it saw a face and did not recognise it | a stranger — or enrol this person, or lower the threshold |
| **a name** | matched, with the score | — |

The score is recorded even when nobody is named. That number is what tells you
whether the threshold is wrong or the face is genuinely a stranger.

### How it stays cheap

Identification runs inside the detection pass, so it inherits every throttle
already there: a still scene never reaches it, and a frame with no person costs
nothing. On top of that, a person is identified **once per visit**. Measured
here: 1.1 ms to look for a face in a person's box, 5.5 ms to turn one into a
vector, against the ~12 ms the detector already spends.

A person is logged the moment they appear, which is usually *before* they turn
towards the camera — so the frames after the arrival are checked too, up to
`face_attempts`, and the name lands on the row that was already written. Once
somebody is identified, or the attempts are spent, nothing more is spent on them
for as long as they stay in view.

Two consequences worth knowing. Retries only happen on frames that pass the
motion gate, so somebody who arrives and stands perfectly still may use fewer
attempts than the budget allows. And with `collapse_repeats` off there are no
tracks to retry against, so identification gets a single look per detection.

### What is stored, and what leaves the box

- **Face embeddings** — a vector per enrolled print, in the add-on's own SQLite,
  plus the 112×112 crop it came from under `/data/faces` so you can see which
  face earned a name.
- **Deleting a person deletes their prints and their crops**, for real. The
  `people` row is kept, marked archived, when old detections still point at it,
  so history stays readable.
- **Embeddings never enter the change feed**, and never will. Names and
  `person_id` do, so the lakehouse can say who was seen — but a biometric
  template copied into Delta history and a Postgres replica could not be recalled
  by deleting it here. Keeping them out is what makes the deletion above an
  honest promise rather than a wish.
- The face crops are small (~4 KB each) and **are** included in Home Assistant
  backups, unlike the detection snapshots. A household's whole enrolment is well
  under a megabyte, and losing it on a restore would mean re-enrolling everybody.

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

**`detection_hub_identified`** fires separately, once per visit, when
identification is on — carrying `person`, `person_id`, `score`, `state`,
`detection_id` and `camera`. Two events rather than a richer detection, because
they are two facts arriving at different times: the detection fires on arrival,
before any face has been seen, and waiting for a name would delay every
detection.

It fires for strangers too, with `person: null` — which is the automation worth
having:

```yaml
automation:
  - alias: "Someone I don't know, at night"
    trigger:
      - platform: event
        event_type: detection_hub_identified
    condition:
      - "{{ trigger.event.data.state == 'unknown' }}"
      - condition: sun
        after: sunset
    action:
      - service: notify.mobile_app
        data:
          message: "Unrecognised person at {{ trigger.event.data.camera }}"

  - alias: "Welcome home"
    trigger:
      - platform: event
        event_type: detection_hub_identified
    condition: "{{ trigger.event.data.person == 'Alice' }}"
    action:
      - service: light.turn_on
        target: {entity_id: light.hall}
```

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

With `identify_people` on, two more appear:

- `sensor.detection_hub_people_identified_today` — the count, with a `by_person`
  breakdown.
- `sensor.detection_hub_last_person` — the last name recognised, with the camera,
  score and time. `never` until somebody is.

There is deliberately **no `binary_sensor.<person>_home`**. A camera sees an
arrival; it never sees a departure. A presence sensor built on that would be
wrong for most of the day, and this add-on does not invent states it cannot
observe.

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

A detection that has been identified shows the **name** where the label would be,
and one that has not says `unrecognised` or `no face seen` — never a blank that
could mean either. Opening a person's snapshot offers **Who is this?**, which is
how somebody gets enrolled.

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

### People and faces

- **`POST /api/faces/probe`** — an image in, and for every person in it: their
  box, whether a face was found, how wide it was, and why it would be refused.
  Stores nothing. This is what the camera check on the page calls.
- **`GET /api/people`** — everyone enrolled, with print counts and the smallest
  enrolled face.
- **`POST /api/people`** `{"name": "Alice"}` — `409` if that name is taken.
- **`PUT /api/people/<id>`** to rename, **`DELETE /api/people/<id>`** to remove.
  The delete reports how many prints went with them.
- **`POST /api/people/<id>/prints`** — enrol a face, either from a stored image
  (`{"snapshot_id": 42, "detection_id": 900}`) or from an uploaded one (raw bytes
  or multipart). A refusal says which problem it is: no face, or a face this
  many pixels wide against the floor.
- **`GET /api/people/<id>/prints`** and **`DELETE /api/people/<id>/prints/<pid>`**
  — list and remove individual prints. The list never includes the vectors.
- **`GET /api/faces/<print_id>`** — the aligned crop a print was made from, so a
  name can be checked against the face that earns it.

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

**Face embeddings are not in the feed either, and for a stronger reason.** The
feed lands in Delta tables with object versioning and a Postgres replica — copies
that a `DELETE` in this add-on can never reach. A biometric template published
there could not be recalled, so "delete a person and their face data is gone"
would stop being true. Names and `person_id` do flow, so the lakehouse can still
say who was seen.

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
- **identify_people**: `false` (default). Put names to the people it detects.
  Off unless you ask for it: it processes biometric data, costs CPU, and on many
  cameras cannot work at all. Run the camera check before turning it on.
  Needs a restart — the models load at startup.
- **face_min_pixels**: `60` (default). How wide a face must be before it is worth
  identifying. Below this the answer is "no usable face" rather than a guess
  computed from a handful of pixels. Set it from what the camera check reports,
  not from hope. Lowering it does not make small faces work — it makes them
  *tried*, which is how you find out where your camera stops.
- **face_match_threshold**: `0.45` (default), 0.20–0.90. How similar two faces
  must be to count as the same person. Higher is stricter. Read on every match,
  so tuning it needs no restart: watch the score on `unrecognised` rows and move
  it if the right person is landing just under.
- **face_margin**: `0.05` (default). How far ahead of the runner-up the best
  match must be. Two people at 0.47 and 0.46 is a coin flip, and neither gets
  named.
- **face_attempts**: `5` (default). How many frames to keep looking for a face
  while somebody is in view. Spent once per visit, and it stops as soon as a face
  is found — so this is what it costs when a person never turns towards the
  camera, not what every visit costs.
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

Detections, cameras, people and the change log live in SQLite at
`/data/detections.db`. **Snapshots do not** — they are files under
`/data/snapshots/`, indexed by a row that holds only their size and dimensions.
Enrolled face crops are files too, under `/data/faces/`, one per print.

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

**Face crops are the exception, and go in backups on purpose.** They are ~4 KB
each and a household's whole enrolment is well under a megabyte, so the reasoning
that excludes snapshots does not apply — while losing them on a restore would
mean re-enrolling everybody. The embeddings are in the database and travel with
it either way.

Enrolments are **outside retention entirely**. `detection_retention_days` and the
snapshot limits are about what the cameras saw; a person you enrolled by hand is
not something a camera saw, so nothing ages them out. If the snapshot a print
came from is pruned, the print survives and only its reference to that image goes
null.

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

## Why these face models

Identification uses two more, both from the OpenCV Zoo and both run through the
same OpenCV that already carries YOLOX — so face recognition adds **no new
dependency** to this add-on at all:

- **YuNet** (`face_detection_yunet_2023mar`, 232 KB, **MIT**) finds faces and the
  five landmarks used to align them. The alignment is not optional: it warps a
  face to the canonical 112×112 the recogniser was trained on, and skipping it
  produces a pipeline that runs, returns plausible numbers and matches nobody.
- **SFace** (`face_recognition_sface_2021dec`, 38.7 MB, **Apache-2.0**) turns an
  aligned face into a 128-value vector.

Both licence texts are vendored beside the weights, taken from opencv_zoo at
commit `47534e27`. Same bar that ruled out Ultralytics for the detector.

**One thing worth stating rather than glossing over:** the zoo does not document
what SFace was trained on. The paper's lineage runs through MS1M-family face
datasets, whose terms are a separate question from the Apache-2.0 licence on the
weights. That is a known unknown here, not a cleared one.

Two versions were rejected on measurement rather than preference. The newer
YuNet (`2026may`) needs OpenCV 5's ONNX Runtime engine, while this add-on's
requirements span OpenCV 4.10 to 5.x — a model that loads on half the supported
range is a trap, not a bundled model. The int8 SFace would have saved 28 MB, and
it loads and then fails inside `feature()` on OpenCV 5.

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
