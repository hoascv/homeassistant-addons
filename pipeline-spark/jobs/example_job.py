"""Example pipeline job, baked into the Pipeline Spark image.

Reads a CSV from MinIO (s3a://), does a tiny aggregation, and writes the result
to Postgres over JDBC. Triggered by the Pipeline Airflow example DAG, but also
runnable by hand:

    spark-submit --deploy-mode cluster --master spark://172.30.32.1:7077 \
        /opt/pipeline/jobs/example_job.py \
        s3a://raw/sample.csv \
        jdbc:postgresql://172.30.32.1:5432/pipeline \
        pipeline_result pipeline <password>

S3A credentials/endpoint come from spark-defaults (set by the add-on).
"""
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "s3a://raw/sample.csv"
    jdbc_url = sys.argv[2] if len(sys.argv) > 2 else "jdbc:postgresql://172.30.32.1:5432/pipeline"
    table = sys.argv[3] if len(sys.argv) > 3 else "pipeline_result"
    pg_user = sys.argv[4] if len(sys.argv) > 4 else "pipeline"
    pg_password = sys.argv[5] if len(sys.argv) > 5 else ""

    spark = SparkSession.builder.appName("pipeline-example").getOrCreate()

    df = (
        spark.read.option("header", True).option("inferSchema", True).csv(input_path)
    )
    result = df.groupBy("category").agg(
        F.count("*").alias("n"),
        F.round(F.avg("value"), 2).alias("avg_value"),
    )

    (
        result.write.format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", table)
        .option("user", pg_user)
        .option("password", pg_password)
        .option("driver", "org.postgresql.Driver")
        .mode("overwrite")
        .save()
    )
    spark.stop()


if __name__ == "__main__":
    main()
