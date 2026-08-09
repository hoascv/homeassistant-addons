# Changelog

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
