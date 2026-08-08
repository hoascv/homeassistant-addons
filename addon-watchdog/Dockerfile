# Base image named directly rather than via BUILD_FROM/build.yaml, which
# Supervisor 2026.04.0 stopped passing. The tag is a multi-arch manifest and
# covers every arch in config.yaml, i386 included.
FROM docker.io/library/python:3.12-slim-bookworm

WORKDIR /app

# Flask and waitress are pure Python, which is the whole dependency policy
# here: the host this targets is i386, and anything needing a compiled wheel
# either has no i686 build or wants a toolchain in the image. Probes use
# stdlib socket/urllib for the same reason.
COPY app/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ .
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
