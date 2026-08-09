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

## Configuration

- **root_user** / **root_password**: the MinIO admin credentials, which double as
  the S3 access key / secret key. **Change the password before starting**, and use
  the same values in the Pipeline Spark and Pipeline Airflow add-ons.
- **default_buckets**: comma-separated buckets to create on start. Leave empty to
  create none.

## Connecting

- **Console (browser):** `http://<home-assistant-host-ip>:9001`.
- **S3 API from other add-ons:** endpoint `http://172.30.32.1:9000` (the default the
  Spark and Airflow add-ons use), access key = `root_user`, secret = `root_password`,
  path-style access, region `us-east-1`.

> **Security:** this publishes the S3 API and console on the host network. Use strong
> credentials and do not expose the host to the internet.
