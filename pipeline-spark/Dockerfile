# Pinned to 4.1.x because Delta Lake publishes no build for Spark 4.2 — its
# newest release (4.3.1) declares spark-sql 4.1.0 as its provided dependency,
# and Delta binds to Catalyst internals that a minor version can move.
FROM docker.io/apache/spark:4.1.3

# HA runs add-ons as root; the upstream image defaults to the 'spark' user.
USER root

ENV SPARK_HOME=/opt/spark

# jq drives the entrypoint; the PostgreSQL JDBC driver lets Spark write to
# Postgres. hadoop-aws (for s3a:// → MinIO) and Delta Lake are pulled at first
# submit via spark.jars.packages — hadoop-aws matched to the image's bundled
# Hadoop version so the AWS SDK transitive dependency is always correct, and
# Delta matched to the Spark minor version it was compiled against.
# hadolint ignore=DL3008,DL4006
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
         -o "${SPARK_HOME}/jars/postgresql-42.7.4.jar" \
    && mkdir -p /opt/pipeline \
    && HADOOP_VER="$(ls "${SPARK_HOME}"/jars/hadoop-client-api-*.jar | sed -E 's#.*/hadoop-client-api-([0-9.]+)\.jar#\1#' | head -1)" \
    && printf 'spark.jars.packages org.apache.hadoop:hadoop-aws:%s,io.delta:delta-spark_4.1_2.13:4.3.1\nspark.master.rest.enabled true\nspark.sql.extensions io.delta.sql.DeltaSparkSessionExtension\nspark.sql.catalog.spark_catalog org.apache.spark.sql.delta.catalog.DeltaCatalog\n' "${HADOOP_VER}" \
         > /opt/pipeline/base-defaults.conf

# Example PySpark job, kept here so it can be run by hand from this container.
# Airflow submits its own copy: Spark standalone runs Python applications in
# client mode only, so the driver — and the .py file it reads — live there.
COPY jobs/example_job.py /opt/pipeline/jobs/example_job.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
