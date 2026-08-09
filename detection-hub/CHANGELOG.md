# Changelog

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
