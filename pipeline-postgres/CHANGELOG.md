# Changelog

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. PostgreSQL 16 for the data-pipeline stack, on host port 5432.
- Provisions a separate `airflow` role and database (on first start) for the
  Pipeline Airflow add-on's metadata store.
- Data persisted in `/data/pgdata`. amd64 only.
