ARG BUILD_FROM
FROM $BUILD_FROM

# jq parses the add-on options file (/data/options.json) in run.sh.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

# Runs once, on first database init, to provision Airflow's metadata DB/role.
COPY init-airflow-db.sh /docker-entrypoint-initdb.d/10-init-airflow-db.sh
COPY run.sh /run.sh
RUN chmod a+x /run.sh /docker-entrypoint-initdb.d/10-init-airflow-db.sh

ENTRYPOINT [ "/run.sh" ]
