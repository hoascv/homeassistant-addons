FROM docker.io/apache/airflow:3.3.0

# --- System dependencies (as root) -----------------------------------------
# The base image runs as the `airflow` user, which cannot write /var/lib/apt.
USER root
# No JVM and no Spark distribution: jobs go to the Pipeline Spark add-on over
# Spark Connect, so this container is a gRPC client and never runs a driver.
# That removes ~700 MB of image and, more to the point, keeps Spark's heap out
# of the container running the scheduler and api-server.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl procps \
    && rm -rf /var/lib/apt/lists/*

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
    "apache-airflow-providers-amazon" \
    "apache-airflow-providers-postgres" \
    "apache-airflow-providers-fab"

# The Spark Connect client: pure Python over gRPC, no JVM, no py4j. Its version
# must match the Pipeline Spark add-on's Spark. delta-spark is here only for
# `delta.connect`, the client half of Delta's Connect support — the server jars
# live in the Spark add-on. --no-deps because delta-spark pins pyspark<=4.1.1
# and would pull the full ~400 MB distribution in on top of the slim client;
# everything it actually imports is satisfied by pyspark-client.
RUN pip install --no-cache-dir "pyspark-client==4.1.3" \
    && pip install --no-cache-dir --no-deps "delta-spark==4.3.1"

# --- Back to root so the add-on can write /data and /share -----------------
USER root
# The DAGs (seeded into /share on boot) and the merge module they import. It
# lives here rather than in the Spark add-on because it is client code now:
# Connect ships the query plan, not the file.
COPY dags/ /opt/airflow/project-dags/
COPY jobs/ /opt/pipeline/jobs/
# Seeded into /share once, for a JupyterLab elsewhere on the machine to pick up.
COPY notebooks/ /opt/pipeline/notebooks/
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
