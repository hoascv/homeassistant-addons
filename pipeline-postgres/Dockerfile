FROM docker.io/library/postgres:16

# jq parses the add-on options file (/data/options.json) in run.sh.
# TimescaleDB comes from Timescale's own apt repo; the Debian codename is read
# from the base image so a codename bump of postgres:16 keeps building.
# pgbackrest needs no new source: the base image already has apt.postgresql.org
# configured (it installs postgresql-16 from there), which carries a far newer
# pgbackrest than Debian's own.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq ca-certificates curl pgbackrest \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
       -o /etc/apt/keyrings/timescaledb.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/timescaledb.asc] https://packagecloud.io/timescale/timescaledb/debian/ ${VERSION_CODENAME} main" \
       > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

# All three run once, on first database init: Airflow's metadata DB/role, the
# TimescaleDB extension in the main pipeline database, then the sensor_test
# role/database/schema for the external timeseries consumer.
COPY init-airflow-db.sh /docker-entrypoint-initdb.d/10-init-airflow-db.sh
COPY init-timescaledb.sh /docker-entrypoint-initdb.d/20-init-timescaledb.sh
COPY init-sensor-db.sh /docker-entrypoint-initdb.d/30-init-sensor-db.sh
COPY run.sh /run.sh
RUN chmod a+x /run.sh /docker-entrypoint-initdb.d/10-init-airflow-db.sh \
    /docker-entrypoint-initdb.d/20-init-timescaledb.sh \
    /docker-entrypoint-initdb.d/30-init-sensor-db.sh

ENTRYPOINT [ "/run.sh" ]
