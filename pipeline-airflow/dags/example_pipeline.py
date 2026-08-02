"""End-to-end example DAG for the data-pipeline stack.

Proves the four add-ons are wired together:
  1. generate a small CSV and upload it to MinIO (bucket `raw`) via the S3 API,
  2. run a Spark job over **Spark Connect** that reads it from `s3a://` and
     writes an aggregate to Postgres over JDBC,
  3. assert the result table in Postgres has rows.

There is no spark-submit here and no Spark installation in this add-on. The
session is a gRPC client; the driver runs inside the Pipeline Spark add-on,
which already holds the S3A credentials and the JDBC driver. Standalone cannot
run a PySpark driver in cluster mode at all, and a client-mode driver would put
Spark's heap next to the scheduler.

Connections (`minio_default`, `pipeline_pg`) and the pipeline Postgres settings
are provided by the add-on via environment variables, so this DAG needs no
manual setup — just unpause and trigger it.
"""
from __future__ import annotations

import csv
import datetime
import io

from airflow.sdk import Variable, dag, task
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.common.sql.operators.sql import SQLCheckOperator

BUCKET = "raw"
KEY = "sample.csv"
RESULT_TABLE = "pipeline_result"
SPARK_CONNECT_URL = "sc://172.30.32.1:15002"


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

    @task
    def spark_transform(input_path: str) -> int:
        # Imported here rather than at module scope: the dag-processor re-parses
        # this folder constantly and needn't pay for pyspark each time.
        from pyspark.sql import SparkSession, functions as F  # noqa: PLC0415

        pg_host = Variable.get("pg_host", default="172.30.32.1")
        pg_port = Variable.get("pg_port", default="5432")
        pg_db = Variable.get("pipeline_pg_db", default="pipeline")
        pg_user = Variable.get("pipeline_pg_user", default="pipeline")
        pg_password = Variable.get("pipeline_pg_password", default="")
        jdbc_url = f"jdbc:postgresql://{pg_host}:{pg_port}/{pg_db}"

        remote = Variable.get("spark_connect_url", default=SPARK_CONNECT_URL)
        spark = (
            SparkSession.builder.appName("pipeline-example").remote(remote).getOrCreate()
        )
        try:
            df = spark.read.option("header", True).option("inferSchema", True).csv(
                input_path
            )
            result = df.groupBy("category").agg(
                F.count("*").alias("n"),
                F.round(F.avg("value"), 2).alias("avg_value"),
            )
            (
                result.write.format("jdbc")
                .option("url", jdbc_url)
                .option("dbtable", RESULT_TABLE)
                .option("user", pg_user)
                .option("password", pg_password)
                .option("driver", "org.postgresql.Driver")
                .mode("overwrite")
                .save()
            )
            return result.count()
        finally:
            spark.stop()

    assert_result = SQLCheckOperator(
        task_id="assert_result",
        conn_id="pipeline_pg",
        sql=f"SELECT count(*) FROM {RESULT_TABLE}",
    )

    spark_transform(generate_and_upload()) >> assert_result


example_pipeline()
