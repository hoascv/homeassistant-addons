# Changelog

## 1.0.0

- First release. PostgreSQL 16 for the data-pipeline stack, on host port 5432.
- Provisions a separate `airflow` role and database (on first start) for the
  Pipeline Airflow add-on's metadata store.
- Data persisted in `/data/pgdata`. amd64 only.
