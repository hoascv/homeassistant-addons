# Must match the primary's image, not merely its Postgres version: physical
# streaming replication ships WAL, so the standby has to load the same
# TimescaleDB build or it will refuse to start on the primary's records.
FROM docker.io/library/postgres:16

# jq parses the add-on options file (/data/options.json) in run.sh.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq ca-certificates curl \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
       -o /etc/apt/keyrings/timescaledb.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/timescaledb.asc] https://packagecloud.io/timescale/timescaledb/debian/ ${VERSION_CODENAME} main" \
       > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

# No initdb hooks here on purpose: a standby's data directory is cloned from the
# primary by pg_basebackup, never initialised locally, so anything in
# docker-entrypoint-initdb.d would never run.
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENTRYPOINT [ "/run.sh" ]
