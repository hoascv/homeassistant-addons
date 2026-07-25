"""End-to-end example DAG for the data-pipeline stack.

Proves the four add-ons are wired together:
  1. generate a small CSV and upload it to MinIO (bucket `raw`) via the S3 API,
  2. submit a PySpark job to the Spark standalone cluster that reads it from
     `s3a://` and writes an aggregate to Postgres over JDBC,
  3. assert the result table in Postgres has rows.

Connections (`minio_default`, `spark_default`, `pipeline_pg`) and the pipeline
Postgres settings are provided by the add-on via environment variables, so this
DAG needs no manual setup — just unpause and trigger it.
"""
from __future__ import annotations

import csv
import datetime
import io

from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.common.sql.operators.sql import SQLCheckOperator

BUCKET = "raw"
KEY = "sample.csv"
RESULT_TABLE = "pipeline_result"


@dag(
    dag_id="example_pipeline",
    schedule=None,
    start_date=datetime.datetime(2026, 1, 1),
    catchup=False,
    tags=["example", "pipeline"],
)
def example_pipeline():
    @task
    def generate_and_upload() -> str:
        rows = [("a", 10), ("b", 20), ("a", 30), ("c", 5), ("b", 15)]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["category", "value"])
        writer.writerows(rows)

        hook = S3Hook(aws_conn_id="minio_default")
        if not hook.check_for_bucket(BUCKET):
            hook.create_bucket(bucket_name=BUCKET)
        hook.load_string(buf.getvalue(), key=KEY, bucket_name=BUCKET, replace=True)
        return f"s3a://{BUCKET}/{KEY}"

    input_path = generate_and_upload()

    pg_host = Variable.get("pg_host", default_var="172.30.32.1")
    pg_port = Variable.get("pg_port", default_var="5432")
    pg_db = Variable.get("pipeline_pg_db", default_var="pipeline")
    pg_user = Variable.get("pipeline_pg_user", default_var="pipeline")
    pg_password = Variable.get("pipeline_pg_password", default_var="")
    job_path = Variable.get("spark_job_path", default_var="/opt/pipeline/jobs/example_job.py")
    jdbc_url = f"jdbc:postgresql://{pg_host}:{pg_port}/{pg_db}"

    spark_transform = SparkSubmitOperator(
        task_id="spark_transform",
        conn_id="spark_default",
        application=job_path,
        deploy_mode="cluster",
        name="pipeline-example",
        application_args=[input_path, jdbc_url, RESULT_TABLE, pg_user, pg_password],
    )

    assert_result = SQLCheckOperator(
        task_id="assert_result",
        conn_id="pipeline_pg",
        sql=f"SELECT count(*) FROM {RESULT_TABLE}",
    )

    input_path >> spark_transform >> assert_result


example_pipeline()
