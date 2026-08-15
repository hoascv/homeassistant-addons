# Network Traffic Monitor

Full packet capture from the Home Assistant host, landed in the datalake so
you can investigate it yourself — an Airflow DAG, a notebook, a query engine
pointed at MinIO, whatever you want to build against it. This add-on's whole
job stops at getting the data safely into `raw/network_traffic/`; nothing
here parses it further than pulling out a handful of fields for the JSONL
sidecar.

> **Read this before starting it.** This add-on runs `tcpdump` with
> `host_network: true` and the `NET_ADMIN`/`NET_RAW` capabilities — the first
> add-on in this repository to ask for either. Depending on your network's
> topology (see *Getting real visibility* below) it may capture traffic
> belonging to every device that reaches it, not just Home Assistant's own —
> plaintext content for protocols that don't encrypt, and metadata (DNS query
> names, TLS SNI) for the ones that do. Both the raw `.pcap` and the parsed
> `.jsonl` land in MinIO's `raw` bucket unencrypted at rest and persist there
> until you delete them, regardless of the local `retention_files` window. If
> this isn't something you have the right or the desire to record — a shared
> household, a guest network, a jurisdiction with wiretapping rules — don't
> install this, or narrow `capture_interfaces`/`bpf_filter` first.

## Getting real visibility

`-i any` with `NET_ADMIN`/`NET_RAW` lets tcpdump see every packet that
reaches the host's own network interfaces. It does **not** make it see every
packet on your LAN. On an ordinary switched home network, a switch only
forwards a port the traffic addressed to it, plus broadcast/multicast — so
what this add-on actually sees depends on where the Home Assistant host sits:

- If the host **is** your router/gateway/access point, or its port is a
  **SPAN/mirror port** on a managed switch, it genuinely sees cross-device
  unicast traffic — the "captures everything" case.
- If it's an ordinary device plugged into a regular switch port (a Pi or a
  NUC, the common case), it sees only **its own traffic** plus
  broadcast/multicast — ARP, DHCP, mDNS, SSDP. That is still real and often
  useful (a lot of IoT discovery chatter is multicast), but it is not "every
  device's traffic."

If you want the broader view, point a mirror/SPAN port at whichever
interface `capture_interfaces` names. Otherwise, expect the narrower one.

## What lands in MinIO

Per completed rotation (every `rotate_seconds`), two objects at
`raw/network_traffic/<YYYY-MM-DD>/<capture_label>-<timestamp>.{pcap,jsonl}`:

- **`.pcap`** — the full capture, exactly what tcpdump wrote, at
  `snap_length` (0 = untruncated). Open it in Wireshark, or reparse it
  however you like later.
- **`.jsonl`** — one JSON object per packet, the fields tshark could pull out
  without decrypting anything: `time`, `src_ip`, `dst_ip`, `ip_proto`,
  `length`, `src_port`, `dst_port`, `dns_query`, `tls_sni`, `http_host`,
  `http_uri`, `protocol`, `info`. This is what you actually want to query —
  the pcap is the fallback for when a field this doesn't carry turns out to
  matter.

The pcap is always uploaded before its jsonl, so a jsonl object never exists
in MinIO without the source pcap backing it up.

## Configuration

- **capture_interfaces** (default `any`): tcpdump's pseudo-interface for
  "every real interface", or a comma-separated list of real interface names
  to narrow it.
- **bpf_filter** (default empty): an optional tcpdump/BPF expression, e.g.
  `not port 22`, to exclude noisy or sensitive traffic. Empty captures
  everything `capture_interfaces` can see.
- **rotate_seconds** (default 300): how often the running capture rotates to
  a new file.
- **retention_files** (default 12): the local ring-buffer size — about an
  hour at the default rotation. Enforced by this add-on's own janitor loop
  rather than tcpdump's `-W`, which on some builds stops capturing entirely
  once the limit is reached rather than wrapping. If MinIO is unreachable
  longer than `rotate_seconds × retention_files`, the oldest unshipped
  captures are discarded rather than filling the disk.
- **snap_length** (default 0): bytes kept per packet. 0 is tcpdump's
  convention for "don't truncate" — the point of full capture is seeing the
  content, so there's little reason to lower this.
- **minio_endpoint** (default `http://172.30.32.1:9000`): the Supervisor
  bridge gateway address, same one pipeline-airflow's own `minio_endpoint`
  option defaults to.
- **minio_access_key** / **minio_secret_key**: Pipeline MinIO's
  `root_user`/`root_password`.
- **minio_bucket** (default `raw`): must already exist or be creatable by
  these credentials — this add-on will create it if missing.
- **minio_prefix** (default `network_traffic`): the key prefix under the
  bucket.
- **datalake_retention_days** (default 7): an S3 lifecycle (expiration) rule
  kept on the bucket, scoped to `minio_prefix` — MinIO deletes captures older
  than this on its own schedule, so the datalake doesn't grow forever under
  continuous full packet capture. `0` disables expiration and keeps
  everything. Scoped to this add-on's own prefix only: the `raw` bucket is
  shared with the trackers' own archived exports, and this never touches
  their rules. As with any S3-style lifecycle rule, expiration isn't instant
  at the exact day boundary — MinIO's own ILM scanner runs periodically, so
  expect deletion within roughly a day of the threshold, not to the second.
- **capture_label** (default empty, meaning the container hostname): included
  in every object's key, useful if this add-on ever runs on more than one
  host writing into the same bucket.
- **restrict_to_user_ids** (default empty, meaning any Home Assistant admin):
  a hard, per-user lock on the ingress panel, on top of `panel_admin: true`.
  Worth setting here more than on almost any other add-on in this repository
  — this one shows you other devices' traffic.

## Pausing capture

The dashboard has a **Pause capture** button — stops `tcpdump` immediately
without stopping the add-on itself, so the dashboard, the upload backlog
drain, and the Add-on Watchdog integration all keep working. Useful when
something needs to stop *right now*: a disk running low, a capture you
didn't mean to leave running, investigating before deciding on a filter.

The pause is written to a flag file under `/data` and survives a restart —
if you press Pause and then update or restart the add-on, it comes back up
still paused rather than silently capturing again. Press **Resume**, or
`POST /api/resume` through ingress, to start it back up.

A pause reports as healthy everywhere — `/api/health`, the dashboard, and the
Add-on Watchdog's status page — since it's a deliberate stop, not `tcpdump`
having died. The upload loop keeps running while paused: anything already
captured still ships to MinIO once it can accept writes again.

## Clearing the datalake

The dashboard's **Danger zone** has a **Clear datalake data** button —
permanently deletes every object this add-on has uploaded, straight from
MinIO. Added for exactly the situation that motivated Pause: a MinIO-full
incident, where reclaiming space quickly matters more than anything already
captured.

It shows the current object count and total size before you can click it,
and the confirmation prompt repeats both — there is no blind "delete
everything, trust me". **This is permanent. There is no undo.**

Scoped to exactly `minio_bucket`/`minio_prefix` and nothing else — the `raw`
bucket is shared with the trackers' own archived exports, and there is no
path in this add-on's code that reaches past its own prefix, even by
mistake. Only already-uploaded objects in MinIO are affected; local files
under `/data/pcap` are never touched, and anything still pending upload just
uploads normally afterward.

## Endpoints

- `/` — the dashboard, refreshing every 15 seconds.
- `/api/status` — capture and lifecycle state, as JSON.
- `/api/health` — what the Add-on Watchdog probes: 200 while `tcpdump` is
  running or deliberately paused, 503 only when it has actually died. The
  dashboard can answer fine with a dead capture process behind it, which is
  exactly why this is a separate endpoint rather than `/`.
- `POST /api/pause` / `POST /api/resume` — what the dashboard's button calls.
- `GET /api/datalake-usage` — object count and total bytes under this
  add-on's own prefix in MinIO. A live MinIO round trip, not cached.
- `POST /api/clear-datalake` — what the Danger zone's button calls. No
  confirmation at the API level; the dashboard's confirm dialog is the only
  thing standing between a call and a real, irreversible delete.

Everything here requires Home Assistant's ingress — there is no published
host port, since nothing outside this add-on needs to reach it directly; the
data goes to MinIO, not out over an API.

## Permissions

`host_network: true` plus `NET_ADMIN` (the promiscuous-mode ioctl tcpdump
issues by default) and `NET_RAW` (the `AF_PACKET` socket capture opens). No
`hassio_api`/`homeassistant_api` — this add-on neither reads Supervisor state
nor pushes Home Assistant sensors, only captures and uploads.

## Notes

- `boot: manual`, deliberately. This is the one add-on in the repository that
  captures traffic beyond its own — it should need a deliberate start, not
  come alive silently on every host boot.
- The rotated `.pcap` buffer under `/data/pcap` is excluded from Home
  Assistant backups (`backup_exclude`), the same reasoning detection-hub uses
  for its snapshots: it is bulky binary data that is either already in MinIO
  or about to be, and a restore does not need to carry a copy of both.
