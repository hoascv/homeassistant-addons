# Pipeline MinIO

MinIO S3-compatible object storage for the data-pipeline stack — the landing zone
and lake for your data. Spark reads/writes it over the `s3a://` scheme and Airflow
talks to it with the S3 API.

Part of a seven-add-on data pipeline. Start them in this order:
`postgres → minio → [metastore] → spark → airflow → notebook`. **Pipeline
Metastore** and **Pipeline Postgres Replica** are optional — nothing needs them
until you want table names instead of paths, or a standby.

## What it does

- Runs MinIO with the **S3 API on host port 9000** and the **web console on 9001**.
- Creates a set of default buckets on start (default `raw`, `staging`, `curated`).
- Stores objects in the add-on's persistent volume (`/data/minio`).
- The console is also in Home Assistant's own sidebar, fronted by a small
  nginx layer that only exists to make that work — see *Sidebar panel* below.

## Configuration

- **root_user** / **root_password**: the MinIO admin credentials, which double as
  the S3 access key / secret key. **Change the password before starting**, and use
  the same values in the Pipeline Spark and Pipeline Airflow add-ons.
- **default_buckets**: comma-separated buckets to create on start. Leave empty to
  create none.

## Connecting

- **Console (browser):** `http://<home-assistant-host-ip>:9001`, or the **MinIO**
  entry in Home Assistant's own sidebar — same console either way.
- **S3 API from other add-ons:** endpoint `http://172.30.32.1:9000` (the default the
  Spark and Airflow add-ons use), access key = `root_user`, secret = `root_password`,
  path-style access, region `us-east-1`.

> **Security:** this publishes the S3 API and console on the host network. Use strong
> credentials and do not expose the host to the internet.

## Sidebar panel

The console is a single-page app that expects to be served from `/`, not
from an arbitrary path — unlike JupyterLab in the Notebook add-on, it has no
`base_url`-style flag to configure that. It works under Home Assistant's
ingress anyway because everything the page needs — API calls, websocket
URLs, every asset path — is derived at runtime from `document.baseURI`,
which comes from one `<base href="/">` tag in the HTML. An internal nginx
layer (port 9002, not published — reaching it any other way than through
ingress is pointless, since :9001 already serves the same console directly)
rewrites just that tag to the ingress path Home Assistant generates, and
strips `X-Frame-Options: DENY`, which MinIO sends by default and which would
otherwise make the browser refuse to render the console inside Home
Assistant's ingress iframe at all.

This is a convenience layer on top of the S3 service, not a replacement for
it: if nginx fails to start — most likely because `hassio_api` couldn't read
this add-on's own ingress path from the Supervisor — the log says so, and
`:9000`/`:9001` keep working exactly as before. The console's own login
(`root_user`/`root_password`) is unchanged and still required through either
door; ingress does not bypass it.
