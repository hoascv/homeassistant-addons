# Base image named directly rather than via BUILD_FROM/build.yaml, which
# Supervisor 2026.04.0 stopped passing. The tag is a multi-arch manifest and
# covers both arches in config.yaml.
FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

# Flask and waitress are pure Python, and the probes use stdlib socket/urllib —
# a deliberately small dependency budget for a component whose job is to keep
# working when other things do not.
COPY app/requirements.txt .
# fio measures the device ceiling on demand. It is the one non-Python
# dependency here, and it earns its place: the alternative is a hand-rolled
# write loop that measures the page cache rather than the disk.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends fio \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir -r requirements.txt

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
