# Base image named directly rather than via BUILD_FROM/build.yaml, which
# Supervisor 2026.04.0 stopped passing. The tag is a multi-arch manifest and
# covers both arches in config.yaml.
FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

# tcpdump does the rotating raw capture; tshark (the Wireshark CLI package)
# turns a completed file into the JSONL sidecar. tshark's own postinst asks a
# debconf question about installing dumpcap setuid-root, for letting non-root
# users in the wireshark group capture — that prompt blocks a non-interactive
# apt install until answered. Preseeded to "false", not the usual "true": this
# container always runs as root and gets NET_ADMIN/NET_RAW from Supervisor's
# `privileged` list directly, so the setuid wrapper buys nothing here and is
# one more setuid binary in the image than necessary.
# hadolint ignore=DL3008
RUN echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections \
    && DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
       tcpdump tshark \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
