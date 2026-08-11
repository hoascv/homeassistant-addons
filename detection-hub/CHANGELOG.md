# Changelog

## 1.11.0-rc1

Not the feature — the instrument that decides whether the feature is worth
building. Identification itself does nothing yet; nothing is stored, no names
exist, and `identify_people` has no effect beyond the settings being readable.

- **"Can it identify people?" on the page.** Check the last person the camera
  saw, or an image you supply, and get an answer in words: *"largest face found:
  34 px. Identification needs 60. This camera will not identify people at this
  distance."* Whether face recognition can work is a property of the **camera**,
  not of this add-on, and an empty result is not something anyone can debug.
- Also at `POST /api/faces/probe`, which stores nothing — same rule `/api/detect`
  follows without `?camera=`.
- **Two models bundled**, both run through the `cv2` already here, so no new
  dependency and still no internet at any point: **YuNet** (MIT) finds faces,
  **SFace** (Apache-2.0) turns one into a vector. The image grows ~39 MB.
- YuNet **2023mar** rather than the newer 2026may, which needs OpenCV 5's ONNX
  Runtime engine while requirements span `>=4.10,<6.0`. The int8 SFace would
  have saved 28 MB and was rejected on measurement, not preference: it loads and
  then fails inside `feature()` on OpenCV 5.
- Measured on this host: YuNet 1.1 ms on a person crop, SFace 5.5 ms per face,
  against the ~12 ms the detector already spends. Cost is not the constraint.
- Settings exist so the floor can be moved while measuring: `identify_people`,
  `face_min_pixels`, `face_match_threshold`, `face_margin`, `face_attempts`.
  Off by default — processing biometric data should be a deliberate act.
- On the frame this repository ships as a fixture, people 74–87 px tall produce
  **no face at all**. That is the honest baseline and the reason this release is
  a measurement rather than a feature.

## 1.10.1

- Documentation fix. **"Why this model" still described Tiny as something you
  supply yourself** via `model_path`, and called the difference between the two
  "no loss that mattered" — both written before 1.10.0 bundled Tiny and measured
  a difference that does matter at distance. The section now says what the nine
  live frames showed and points at the `model` option, instead of contradicting
  it eighty lines further down.

## 1.10.0

- **The detection model is selectable.** A new `model` option takes `nano` (the
  default, unchanged) or `tiny` — the larger YOLOX network, now bundled in the
  image. Both are 416×416 and emit the same tensor shape, so the letterbox, the
  grid decode and the NMS are identical: only the weights differ.
- **Measured on the real camera before shipping, rather than assumed.** Over 9
  driveway frames both models found the near car every time (nano 0.88, tiny
  0.90), but the *second, more distant* car sat on the 0.6 threshold for nano —
  2 frames at 0.60–0.64 — where tiny found it in 3 and scored up to 0.86. That
  marginal case is the whole benefit, and it costs 2–4× the CPU per analysed
  frame. Nano remains the default because that trade should be a decision.
- `model_path` still overrides `model`, and an unrecognised model name falls
  back to the default rather than leaving the add-on unable to detect anything.
- The running model is reported on the status card and in `/api/debug`, so
  "did my change take effect" is answerable — it needs a restart, like every
  option the detector and cameras read at startup.
- The image grows ~20 MB for the bundled model. Vendored rather than downloaded
  at build time, so the add-on still needs no internet at any point.

## 1.9.0

- **Filter detections by object type.** An Object dropdown on the page shows only
  the types actually detected, backed by a new `label` parameter on
  `/api/detections` (one type or several, comma-separated) and a `/api/labels`
  endpoint listing what has been seen, most common first.
- **A cleaner page, and no more wrestling the calendar.** The date filter is now
  a row of buttons — Live · 1h · Today · 7 days · Custom — so the common windows
  are one click and the native date-time picker only appears under Custom. The
  rest of the page was redesigned too: a status strip, a proper featured "last
  detected" with a confidence pill, and a tidier thumbnail grid with hover.
- The object and time filters combine, and both fold into the API, so the same
  narrowing is available to a script.

## 1.8.0

- **The date filter now includes time.** The From / To fields are date-and-time
  pickers, so you can narrow to a window within a day rather than a whole day at
  a time. `/api/detections` `from`/`to` accept a date (`2026-08-09`), a
  date-and-minute (`2026-08-09T18:00`), or a full timestamp; a date-only bound
  still covers midnight to midnight, so existing date-only use is unchanged.
- An impossible date or time (`2026-13-40`, `T25:99`) is a 400 rather than a
  filter that quietly matches nothing — the shape *and* the values are checked.

## 1.7.0

- **Click a snapshot to expand it.** The sidebar page opens any detection full
  size with its bounding box drawn on and the label, camera, confidence and time
  beneath — click outside or press Escape to close.
- **Browse past detections by date.** From / To fields filter the list to a day
  or a range (either bound works alone; Clear returns to the live view), backed
  by new `from`/`to` parameters on `/api/detections`. A malformed date is a 400
  rather than a silent all-results.
- Both build only on data already stored — the box coordinates and `detected_at`
  were always there; nothing new is recorded.

## 1.6.0

- **A stationary object is logged once, not on every frame.** A parked car was
  re-detected whenever the wind, the light or the camera's exposure tripped the
  motion gate — the old rule suppressed a label only for a fixed window and then
  re-fired, and time cannot tell "same car, still parked" from "a new car
  arrived". Detections are now matched to recent ones of the same label by box
  overlap: a match is the same object still present and is suppressed however
  long it stays, while something in a new position, or the same spot after it
  emptied, is a fresh event. Expiry is driven by absence from the frames the
  detector actually looks at, so a car on a still scene is never wrongly declared
  gone and re-logged.
- **New `collapse_repeats` option (default on)** to turn that off and log every
  detection, for anyone who wants the raw stream.
- `cooldown_seconds` keeps its name but now means how long an object must be
  *absent* before a reappearance counts as new, rather than a fixed re-fire
  timer.

## 1.5.0

- The sidebar page now shows **Last detected**: the most recent detection with
  its snapshot, camera, confidence and time, and a strip of recent ones,
  refreshing every ten seconds. It reads the `/api/detections` and
  `/api/snapshots` endpoints that already existed, so it is a live view of what
  the cameras see without opening Developer Tools. A detection whose snapshot
  was already pruned shows without a thumbnail rather than a broken image.

## 1.4.0

- **Camera-offline push notifications.** Set `notify_service` and you get a push
  when a camera drops and another when it recovers — on the transition, so a
  camera down for an hour is one message rather than sixty. Offline includes a
  **frozen feed**: a camera delivering no frame for `camera_offline_seconds`
  (default 120) counts as down even if its thread never errored, which a plain
  connection check would miss. A camera offline at first sight is recorded
  without paging — the alert is for one that was working and stopped.
- **A positive log line when a camera connects**, with the stream resolution:
  `camera driveway: connected (640x360)`. A working camera used to produce only
  silence, indistinguishable from one that never started — and the resolution is
  otherwise nowhere, since many cameras' RTSP handshake does not advertise it.
- **Readable connection failures.** A failed open now names the likely causes —
  a rejected password, a wrong path, an unreachable camera — rather than leaving
  only ffmpeg's raw error. On some cameras a wrong password surfaces as `406 Not
  Acceptable`, which reads like a protocol fault; the message now says so.

## 1.3.0

- **Snapshots are files now, not blobs in the database, and they are excluded
  from Home Assistant backups.** Measured at ~68 KB per 768×576 frame, the
  2000-image default was putting ~136 MB of pictures into every backup — and a
  1080p stream roughly four times that — forever, for images glanced at once.
  They live under `/data/snapshots/` and `config.yaml` carries a
  `backup_exclude` for that directory. A restored backup brings back every
  detection and none of the pictures; `/api/snapshots/<id>` returns 404 for
  those, which is a normal state rather than a fault.
- Existing images are **migrated**, not discarded: an older database has its
  `image` column written out to files on the next boot, then the column is
  dropped.
- `/api/stats` reports `snapshot_bytes` alongside `db_bytes`. The two are backed
  up differently, and one combined size would hide the distinction that matters.
- A failed image write now costs the picture and not the detection: the
  half-made row is removed and the detection is stored with a null
  `snapshot_id`, which the schema already allowed for.
- **WAL and `synchronous=NORMAL`.** Measured on four concurrent writers plus a
  reader: ~2,160 commits/s against ~1,500 on the default rollback journal, a 44%
  gain with no errors either way. This is tuning, not a repair — see below.
- The image directory now follows the connection rather than a module-level
  default, so two databases in one process cannot write pictures into each
  other's directory.

**A correction.** This release was planned around a claimed bug: that
`busy_timeout` defaulted to 0, so a contended write would fail immediately.
That was wrong. Python's `sqlite3.connect` defaults to `timeout=5.0`, so it was
already 5000 ms, and a reproduction with four concurrent writers and a reader
found no errors and no lost rows on the old configuration. WAL is a real
improvement and worth keeping, but nothing was broken. The backup problem was
the real one, and it was measured.

## 1.2.0

- **Watches RTSP cameras.** One per line under `cameras`, as `name = url`.
- **Motion gating, which is what makes this affordable on a CPU.** A forward
  pass costs ~15 ms, so a 15 fps stream fed straight to the detector is a
  quarter of a core spent re-deciding that nothing changed. Frames are sampled
  to `max_fps` (default 2), compared against the previous one at 320 px grey,
  and only the ones that differ reach the model. A still scene costs a
  comparison rather than an inference.
- **A cooldown per camera per label** (default 30s), so a person standing in
  view is one event rather than sixty — protecting the database and Home
  Assistant's recorder rather than the CPU.
- **Fires `detection_hub_detection` events**, which is new ground for this
  repository: everything here until now set states. A detection happens at an
  instant, and as a state two in a row look like one.
- **Sensors**: detections today with a per-label breakdown, cameras online, and
  per-camera last-seen plus a `connectivity` binary sensor — because a camera
  that is up but seeing nothing and one that is down are different situations.
- **Writes `/share/pipeline-status/detection-hub.json`** for the Add-on
  Watchdog, reporting not-ok when the detector is broken or a configured camera
  is not streaming. Neither is visible to an HTTP probe: the web UI answers
  perfectly well while a dead thread watches nothing, which is the exact failure
  that file exists to surface.
- RTSP is forced over TCP. UDP over wifi yields torn frames, which the motion
  gate reads as movement and the detector then wakes for, all night.
- A camera that drops reconnects with a widening backoff to a minute, so a
  rebooting camera is not retried in a tight loop.

## 1.1.0

- **Remembers what it saw.** A SQLite store at `/data/detections.db`: detections,
  snapshots, and the cameras that reported them.
- **`POST /api/detect?camera=<name>`** records the detection and a snapshot
  under that name. Without the parameter nothing is stored, so the try-it page
  cannot fill the database — but with it, the HTTP API is an input source on the
  same footing as a camera will be.
- **The change feed the pipeline already knows how to read**: `/api/export`,
  `/api/changes?since=`, `/api/stats`, the same contract the trackers expose.
  Written by SQLite triggers rather than by the routes, because `app.py` will
  grow write paths and a trigger cannot be forgotten.
- **Snapshots are structurally excluded from the feed.** Images live in their
  own untracked table rather than as a column, so a change event stays small by
  construction instead of by remembering to filter a blob out. The reference
  survives, so a consumer can fetch the picture by id.
- Retention bounded two ways — `detection_retention_days` (30),
  `snapshot_retention_days` (7) and `snapshot_max_count` (2000). Either limit
  alone fails: a quiet week keeps images longer than intended, a busy hour blows
  past any size expectation well inside the age window. `/data` is inside Home
  Assistant's backups, which is what makes the ceiling matter.
- Detections outlive their snapshots. The reference is nullable and pruning
  clears it, rather than leaving rows pointing at images that are gone.

## 1.0.0

- First release. Object detection on the CPU: `POST /api/detect` takes an image
  and returns labels, confidences and pixel boxes, plus a drop-an-image page
  behind ingress for checking it by eye.
- **YOLOX-Nano, bundled in the image** — 3.6 MB, Apache-2.0, ~15–20 ms per
  image. YOLOv5 and YOLOv8 were ruled out on licence (Ultralytics AGPL-3.0, the
  wrong thing to vendor into a public repository), and YOLOX-Tiny was measured
  against Nano on the same frame: identical people and vehicles found, 51 ms
  against 14 ms, five times the file size. `model_path` can still swap it.
- Confidence defaults to **0.6** rather than something looser. At 0.35 a dark
  post in the test footage scored 0.50 as a person.
- `labels` defaults to a short list rather than all 80 COCO classes, which are
  noisier than they sound indoors.
- The published port requires `api_token` — including when none is configured,
  in which case it refuses everything. Ingress is unaffected. Same rule the
  trackers settled on in Coop Tracker 1.44.0, applied from the first release
  rather than four versions in.
- `/api/health` answers **503** when the model will not load. The Add-on
  Watchdog counts anything below 500 as alive, so a detector that cannot detect
  has to say so above that line.
- Cameras, stored detections, Home Assistant sensors and events, and the feed
  into the data pipeline are the releases that follow. This one is the piece
  they all depend on, shipped once proven on its own.
