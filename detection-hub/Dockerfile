# Base image named directly rather than via BUILD_FROM/build.yaml, which
# Supervisor 2026.04.0 stopped passing.
FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

# opencv-python-headless still links a few system libraries even in the headless
# build. Without these the import fails at runtime with a bare "libGL.so.1: cannot
# open shared object file", which looks like a Python problem and is not.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Requirements before source so the pip layer survives an app.py edit — opencv
# is a ~60 MB wheel and rebuilding it on every change is a slow way to work.
COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
