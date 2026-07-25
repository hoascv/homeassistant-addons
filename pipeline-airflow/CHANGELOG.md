# Changelog

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. Apache Airflow 3.3 (LocalExecutor), web UI on host port 8085.
- Uses the Pipeline Postgres add-on for its metadata database.
- Pre-wired connections `minio_default` (S3), `spark_default`, and `pipeline_pg`,
  plus an `example_pipeline` DAG that runs MinIO → Spark → Postgres end to end.
- Bundled Spark client for `SparkSubmitOperator`. DAGs live in
  `/share/pipeline-airflow/dags`. amd64 only.
