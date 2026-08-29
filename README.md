# Home Assistant Add-ons

Personal Home Assistant add-on repository.

## Add-ons

- **[Coop Tracker](coop-tracker/DOCS.md)** — log egg collection, coop
  cleaning, and feeding for your chickens from your phone.
  ([architecture](coop-tracker/ARCHITECTURE.md))
- **[Goal Tracker](gym-tracker/DOCS.md)** — track a weight / body-fat goal,
  home workouts, and a daily challenge, with reminders through a Home
  Assistant notify service.
- **[Detection Hub](detection-hub/DOCS.md)** — object detection on the CPU:
  watches RTSP cameras, answers on demand over HTTP, fires a Home Assistant
  event the moment something appears, and keeps the history for the pipeline.
  Optionally puts a **name** to the people it sees, if your camera shows faces
  big enough — it has a check that tells you. amd64 only.
- **[Add-on Watchdog](addon-watchdog/DOCS.md)** — whether the add-ons here are
  actually working, not merely running: a status page plus a sensor per add-on,
  with a probe that catches a live container around a dead service. It also
  records device I/O continuously, so a slow window can be evidenced after the
  fact — see [Is the storage the limit?](STORAGE-IO.md).
- **[Electricity Tracker](electricity-tracker/DOCS.md)** — Danish day-ahead
  electricity spot prices (DK1/DK2) combined with your own smart-meter
  consumption from Eloverblik into a full end-user price: spot + your grid
  company's tariff + Energinet's transmission tariff + elafgift + VAT.
  Works as a price-only tracker before Eloverblik is configured.
- **[Knowledge](knowledge/DOCS.md)** — a topic a day: subscribe to what you
  want to learn and get a syllabus, a briefing, a self-grading quiz, written
  questions, a practical task and spaced-repetition flashcards. It never calls
  an LLM itself — it writes you a prompt to run anywhere you have a connection,
  and you paste the reply back, so the studying works with no internet at all.
- **[Journal](journal/DOCS.md)** — an encrypted daily journal: semi-structured
  entries behind a master password, goals you check in against day by day, and
  any past date a tap away. Everything written is AES-256-GCM at rest under a
  key derived from a password the add-on never stores, so the database, and the
  backup it sits in, are unreadable without it — and there is no recovery if
  you forget it. Ingress only, with a streak sensor that carries counts and
  dates but never a word of the content.
- **[Network Traffic Monitor](network-traffic/DOCS.md)** — full packet capture
  from the host: a rotating raw `.pcap` plus a parsed `.jsonl` record per
  packet (DNS queries, TLS SNI, plaintext HTTP where present), shipped
  straight to **Pipeline MinIO**'s `raw` bucket for your own Airflow DAG to
  pick up later. Only needs MinIO running to upload — no other order
  dependency. The first add-on here to run with `host_network` and elevated
  capabilities; read its Security section before installing.

### Data pipeline (amd64 only)

A pre-wired data-engineering stack, one service per add-on. Meant for an
**amd64 host with 8–16 GB+ RAM** — not a Raspberry Pi. Install and **start them
in this order**: `postgres → minio → [metastore] → spark → airflow → notebook`.

- **[Pipeline Postgres](pipeline-postgres/DOCS.md)** — PostgreSQL 16 with
  TimescaleDB (pipeline DB + Airflow metadata DB).
- **[Pipeline MinIO](pipeline-minio/DOCS.md)** — MinIO S3-compatible object
  storage, with its console in Home Assistant's own sidebar as well as on
  the LAN directly.
- **[Pipeline Spark](pipeline-spark/DOCS.md)** — Apache Spark 4.1 single-node
  cluster with a Spark Connect server.
- **[Pipeline Airflow](pipeline-airflow/DOCS.md)** — Apache Airflow 3.3 orchestrator,
  pre-wired to the above, with DAGs that load the trackers into Delta tables.
- **[Pipeline Notebook](pipeline-notebook/DOCS.md)** — JupyterLab opening on the
  pipeline, with the Spark Connect client and the pipeline modules ready to
  import. Ingress only.
- **[Pipeline Metastore](pipeline-metastore/DOCS.md)** — optional Hive metastore,
  so the Delta tables have names instead of paths. Nothing uses it until Spark's
  `metastore_uris` points at it.
- **[Pipeline Postgres Replica](pipeline-postgres-replica/DOCS.md)** — optional
  streaming read-only standby on port 5433, for read queries and fast promotion.
  Not a backup: the primary's `backup_enabled` covers that.

The add-ons reach each other over the host gateway (`172.30.32.1`) using each
service's published port; every connection target is an overridable option. The
trackers are the exception — Airflow finds them by add-on hostname, so they need
no published port at all.

## Installing this repository

1. In Home Assistant: **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top right) → **Repositories**.
3. Add the URL of this repository (once pushed to a Git host, e.g.
   `https://github.com/hoascv/homeassistant-addons`).
4. Find **Coop Tracker** or **Goal Tracker** in the store and install it.

### Testing locally without Git

If your Home Assistant host exposes a `/addons` share (e.g. via the Samba or
SSH & Web Terminal add-on), copy an add-on's folder there directly:

```
/addons/coop-tracker/
/addons/gym-tracker/
```

Then go to **Settings → Add-ons → Add-on Store**, click **⋮ → Check for
updates**, and the add-on will appear under "Local add-ons".
