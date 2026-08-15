# Changelog

## 0.4.0

- The dashboard's "Danger zone" has a **Clear datalake data** button, for
  reclaiming space directly from a MinIO-full incident without leaving the
  add-on. Shows a live object count and size first, requires an explicit
  confirmation naming both before deleting anything, and is permanent —
  there is no undo.
- Scoped to exactly this add-on's own prefix (`minio_bucket`/`minio_prefix`)
  and nothing else — the `raw` bucket is shared with the trackers' own
  archived exports, and there is no code path anywhere in this add-on that
  can reach past its own prefix, even by mistake. Verified against a real
  MinIO: objects under a different source's prefix survive a clear
  untouched.
- Local files are never touched by this — only already-uploaded objects in
  MinIO. Anything still pending upload just uploads normally afterward.

## 0.3.0

- The dashboard has a **Pause capture** / **Resume capture** button. Pausing
  stops `tcpdump` and keeps it stopped — including across a Supervisor
  restart, via a flag file under `/data` — until you press Resume or call
  `POST /api/resume`. Added after a real MinIO-full incident: with the
  disk critically low, pausing from the dashboard was the fastest way to stop
  the local pcap buffer growing further, without needing to stop the whole
  add-on (which would also take down its own status page).
- The upload/lifecycle loop keeps running while paused, so any already-
  captured backlog still drains once MinIO can accept writes again — only
  the capture side pauses.
- A pause reports as **healthy**, not degraded, to both `/api/health` and the
  `/share/pipeline-status` report the Add-on Watchdog reads — the same
  distinction already drawn for a `boot: manual` add-on found stopped on
  purpose. Reporting a deliberate pause as a fault would train whoever
  pressed the button to ignore the alert the next time it fires for a real
  crash.

## 0.2.0

- Added `datalake_retention_days` (default 7): an S3 lifecycle rule kept on
  MinIO's `raw` bucket, scoped to this add-on's own prefix, so old captures
  expire on MinIO's own schedule instead of accumulating forever. `0`
  disables it. `retention_files` already bounded the local buffer before
  upload; nothing previously bounded how long uploaded data sat in the
  datalake itself, and continuous full packet capture left unbounded there
  grows without limit.
- The rule is merged into whatever lifecycle configuration already exists on
  the bucket, touching only the rule carrying this add-on's own ID — the
  `raw` bucket is shared with the trackers' own archived exports, and this
  never overwrites their configuration.

## 0.1.0

- First release. Runs `tcpdump` against the Home Assistant host's network,
  rotating to a new full-snaplen `.pcap` under `/data/pcap` on a timer
  (`rotate_seconds`, default 300s), with a local ring buffer
  (`retention_files`, default 12 — about an hour) enforced by this add-on's
  own janitor loop rather than tcpdump's own `-W`, which on some builds stops
  capturing entirely at the limit instead of wrapping.
- Each completed rotation is run through `tshark` into a JSONL sidecar — one
  record per packet, with the 5-tuple plus DNS query name, TLS SNI, and
  plaintext HTTP host/path where the protocol exposes them. Both the `.pcap`
  and the `.jsonl` are uploaded straight to Pipeline MinIO's `raw` bucket
  (`s3://raw/network_traffic/<date>/...`) and deleted locally once the upload
  succeeds.
- A small ingress dashboard reports capture health (running/restarts),
  average throughput for the last rotation, pending/uploaded/discarded file
  counts, and local disk headroom.
- Reports into `/share/pipeline-status/network-traffic.json`, so the Add-on
  Watchdog can tell a dead `tcpdump` process apart from a dashboard that is
  merely answering.
- **First add-on in this repository to set `host_network: true` and request
  `NET_ADMIN`/`NET_RAW`.** It needs both to see the host's real network
  interfaces rather than a namespaced virtual one — read DOCS.md's Security
  section, including the note on what a switched network actually lets it
  see, before installing this.
