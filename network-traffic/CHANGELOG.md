# Changelog

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
