FROM docker.io/library/python:3.12-slim

# jq drives the entrypoint; curl reads this add-on's ingress path from the
# Supervisor API at startup.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/*

# No JVM and no Spark distribution. The Spark Connect client is pure Python over
# gRPC, so a notebook can drive the cluster in the Pipeline Spark add-on without
# any of it — the same reason the Airflow add-on shed ~700 MB.
#
# pyspark-client must match the Pipeline Spark add-on's Spark version, and
# delta-spark must match its Delta jars. --no-deps on delta-spark because it
# pins pyspark<=4.1.1 and would otherwise pull the full distribution in on top
# of the slim client; everything it imports is satisfied by pyspark-client.
# pandas, pyarrow and numpy arrive as pyspark-client dependencies, so .toPandas()
# and ordinary DataFrame work need nothing further.
RUN pip install --no-cache-dir \
        "jupyterlab>=4.2,<5" \
        "pyspark-client==4.1.3" \
    && pip install --no-cache-dir --no-deps "delta-spark==4.3.1"

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT [ "/run.sh" ]
