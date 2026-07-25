FROM docker.io/apache/spark:4.2.0

# HA runs add-ons as root; the upstream image defaults to the 'spark' user.
USER root

ENV SPARK_HOME=/opt/spark

# jq drives the entrypoint; the PostgreSQL JDBC driver lets Spark write to
# Postgres. hadoop-aws (for s3a:// → MinIO) is pulled at first submit via
# spark.jars.packages, matched to the image's bundled Hadoop version so the
# AWS SDK transitive dependency is always correct.
# hadolint ignore=DL3008,DL4006
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
         -o "${SPARK_HOME}/jars/postgresql-42.7.4.jar" \
    && mkdir -p /opt/pipeline \
    && HADOOP_VER="$(ls "${SPARK_HOME}"/jars/hadoop-client-api-*.jar | sed -E 's#.*/hadoop-client-api-([0-9.]+)\.jar#\1#' | head -1)" \
    && printf 'spark.jars.packages org.apache.hadoop:hadoop-aws:%s\nspark.master.rest.enabled true\n' "${HADOOP_VER}" \
         > /opt/pipeline/base-defaults.conf

# Example PySpark job, baked in so a cluster-mode submit finds it on the worker.
COPY jobs/example_job.py /opt/pipeline/jobs/example_job.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
