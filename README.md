# Home Assistant Add-ons

Personal Home Assistant add-on repository.

## Add-ons

- **[Coop Tracker](coop-tracker/DOCS.md)** — log egg collection, coop
  cleaning, and feeding for your chickens from your phone.
  ([architecture](coop-tracker/ARCHITECTURE.md))
- **[Gym Tracker](gym-tracker/DOCS.md)** — track a weight / body-fat goal,
  home workouts, and a daily challenge, with reminders through a Home
  Assistant notify service.

### Data pipeline (amd64 only)

A pre-wired data-engineering stack, one service per add-on. Meant for an
**amd64 host with 8–16 GB+ RAM** — not a Raspberry Pi. Install and **start them
in this order**: `postgres → minio → spark → airflow → notebook`.

- **[Pipeline Postgres](pipeline-postgres/DOCS.md)** — PostgreSQL 16 with
  TimescaleDB (pipeline DB + Airflow metadata DB).
- **[Pipeline MinIO](pipeline-minio/DOCS.md)** — MinIO S3-compatible object storage.
- **[Pipeline Spark](pipeline-spark/DOCS.md)** — Apache Spark 4.1 single-node
  cluster with a Spark Connect server.
- **[Pipeline Airflow](pipeline-airflow/DOCS.md)** — Apache Airflow 3.3 orchestrator,
  pre-wired to the above, with DAGs that load the trackers into Delta tables.
- **[Pipeline Notebook](pipeline-notebook/DOCS.md)** — JupyterLab opening on the
  pipeline, with the Spark Connect client and the pipeline modules ready to
  import. Ingress only.

The add-ons reach each other over the host gateway (`172.30.32.1`) using each
service's published port; every connection target is an overridable option. The
trackers are the exception — Airflow finds them by add-on hostname, so they need
no published port at all.

## Installing this repository

1. In Home Assistant: **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top right) → **Repositories**.
3. Add the URL of this repository (once pushed to a Git host, e.g.
   `https://github.com/hoascv/homeassistant-addons`).
4. Find **Coop Tracker** or **Gym Tracker** in the store and install it.

### Testing locally without Git

If your Home Assistant host exposes a `/addons` share (e.g. via the Samba or
SSH & Web Terminal add-on), copy an add-on's folder there directly:

```
/addons/coop-tracker/
/addons/gym-tracker/
```

Then go to **Settings → Add-ons → Add-on Store**, click **⋮ → Check for
updates**, and the add-on will appear under "Local add-ons".
