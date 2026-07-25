FROM docker.io/apache/airflow:3.3.0

# --- System dependencies (as root) -----------------------------------------
USER root
# jq (options), Java + a Spark client so SparkSubmitOperator can submit to the
# standalone master, and procps for the entrypoint's process handling.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl procps openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV SPARK_HOME=/opt/spark
ENV PATH=$SPARK_HOME/bin:$PATH
# hadolint ignore=DL4006
RUN curl -fsSL https://archive.apache.org/dist/spark/spark-4.2.0/spark-4.2.0-bin-hadoop3.tgz -o /tmp/spark.tgz \
    && mkdir -p /opt/spark \
    && tar -xzf /tmp/spark.tgz -C /opt/spark --strip-components=1 \
    && rm /tmp/spark.tgz

# --- Providers (must be installed as the airflow user) ---------------------
# The image installs Airflow into /home/airflow/.local and rejects `pip` as
# root, so switch users. run.sh sets HOME=/home/airflow so the add-on, run as
# root under Supervisor, still imports these.
# hadolint ignore=DL3013
USER airflow
RUN pip install --no-cache-dir \
    "apache-airflow-providers-apache-spark" \
    "apache-airflow-providers-amazon" \
    "apache-airflow-providers-postgres"

# --- Back to root so the add-on can write /data and /share -----------------
USER root
# Example DAG (seeded into /share on first boot) and the PySpark job it submits
# (baked here too so a cluster-mode spark-submit can read the local path).
COPY dags/ /opt/airflow/project-dags/
COPY jobs/example_job.py /opt/pipeline/jobs/example_job.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
