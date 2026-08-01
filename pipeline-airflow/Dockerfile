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
# Must match the Spark add-on's version: this is the client that submits to it.
RUN curl -fsSL https://archive.apache.org/dist/spark/spark-4.1.3/spark-4.1.3-bin-hadoop3.tgz -o /tmp/spark.tgz \
    && mkdir -p /opt/spark \
    && tar -xzf /tmp/spark.tgz -C /opt/spark --strip-components=1 \
    && rm /tmp/spark.tgz

# --- Providers (must be installed as the airflow user) ---------------------
# The image installs Airflow into /home/airflow/.local and rejects `pip` as
# root, so switch users. run.sh sets HOME=/home/airflow so the add-on, run as
# root under Supervisor, still imports these.
# hadolint ignore=DL3013
USER airflow
# providers-fab is not optional here: without it Airflow 3 falls back to
# SimpleAuthManager, which ignores the admin_user/admin_password options
# entirely, generates a password it prints once to the log, and stores it in
# plaintext — its own source calls it dev-only. The UI is published on the host
# network, so it needs a real auth manager.
RUN pip install --no-cache-dir \
    "apache-airflow-providers-apache-spark" \
    "apache-airflow-providers-amazon" \
    "apache-airflow-providers-postgres" \
    "apache-airflow-providers-fab"

# The tracker merge job runs its driver here — Spark standalone cannot run a
# Python application in cluster deploy mode, so submits are client mode — which
# means `import delta.tables` has to resolve in *this* image, not only on the
# workers. Keep this version equal to the delta-spark jar coordinate in run.sh
# and in the Pipeline Spark add-on. --no-deps because it pins pyspark<=4.1.1
# while this client is 4.1.3; spark-submit puts the matching pyspark on
# PYTHONPATH from SPARK_HOME anyway.
RUN pip install --no-cache-dir --no-deps "delta-spark==4.3.1"

# --- Back to root so the add-on can write /data and /share -----------------
USER root
# The DAGs (seeded into /share on boot) and the PySpark jobs they submit. The
# jobs live here rather than in the Spark add-on because the driver is local.
COPY dags/ /opt/airflow/project-dags/
COPY jobs/ /opt/pipeline/jobs/
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
